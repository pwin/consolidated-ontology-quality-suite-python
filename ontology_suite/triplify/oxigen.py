"""Shells out to a built ``oxi-gen`` binary to triplify CSV files via SPARQL
CONSTRUCT queries (tarql-compatible syntax) -- the actual triplification
engine this suite assesses the *output* and *query shape* of, invoked as an
external process rather than reimplemented. See ``config.find_oxi_gen_binary``
for how the binary is located and ``docs/ARCHITECTURE.md`` for why oxi-gen is
referenced as a sibling repo rather than vendored.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .discovery import TriplifyJob


class OxiGenNotFoundError(RuntimeError):
    pass


class OxiGenRunError(RuntimeError):
    def __init__(self, job: TriplifyJob, returncode: int, stderr: str):
        self.job = job
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"oxi-gen exited {returncode} for {job.csv_path.name} + {job.query_path.name}: {stderr.strip()}"
        )


@dataclass
class TriplifyResult:
    job: TriplifyJob
    output_path: Path
    stdout: str
    stderr: str


def run_oxi_gen(
    binary: str | Path,
    job: TriplifyJob,
    output_path: str | Path,
    *,
    delimiter: Optional[str] = None,
    tab: bool = False,
    no_header_row: bool = False,
    normalize: bool = False,
    ntriples: bool = False,
    dedup: Optional[int] = None,
    test_rows: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> TriplifyResult:
    """Run one CSV+query pair through oxi-gen, writing Turtle (or N-Triples,
    with ``ntriples=True``) to ``output_path``.

    Raises ``OxiGenRunError`` (with the process's stderr attached) if oxi-gen
    exits non-zero, so a bad query or malformed CSV surfaces as a clear
    pipeline-stage failure rather than a silently-empty output file.
    """
    binary = Path(binary)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        str(binary),
        "-q", str(job.query_path),
        "-i", str(job.csv_path),
        "-o", str(output_path),
    ]
    if delimiter is not None:
        args += ["-d", delimiter]
    if tab:
        args.append("--tab")
    if no_header_row:
        args.append("--no-header-row")
    if normalize:
        args.append("--normalize")
    if ntriples:
        args.append("--ntriples")
    if dedup is not None:
        args.append(f"--dedup={dedup}")
    if test_rows is not None:
        args.append(f"--test={test_rows}")
    if extra_args:
        args += extra_args

    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise OxiGenRunError(job, proc.returncode, proc.stderr)
    return TriplifyResult(job=job, output_path=output_path, stdout=proc.stdout, stderr=proc.stderr)


def run_oxi_gen_batch(
    binary: str | Path,
    jobs: List[TriplifyJob],
    out_dir: str | Path,
    **run_kwargs,
) -> tuple[List[TriplifyResult], List[OxiGenRunError]]:
    """Run every job in ``jobs`` through oxi-gen, writing ``<csv-stem>.ttl``
    (or ``.nt``) into ``out_dir`` for each. Failures are collected rather
    than raised immediately, so one bad CSV/query pair doesn't abort an
    entire batch -- check the returned error list."""
    out_dir = Path(out_dir)
    suffix = ".nt" if run_kwargs.get("ntriples") else ".ttl"
    results: List[TriplifyResult] = []
    errors: List[OxiGenRunError] = []
    for job in jobs:
        output_path = out_dir / f"{job.csv_path.stem}{suffix}"
        try:
            results.append(run_oxi_gen(binary, job, output_path, **run_kwargs))
        except OxiGenRunError as exc:
            errors.append(exc)
    return results, errors
