"""Triplifies CSV files with tarka, the TARQL engine in SemOps System.

tarka runs TARQL as written for oxi-gen -- the SPARQL ``CONSTRUCT`` dialect
this suite's sketches, consistency checks and repairs are all about -- and
takes oxi-gen's options under the same names. It ships in ``semops-system``,
the wheel the suite already takes HOLOS and the SHACL engine from, so
triplifying no longer needs a separately built binary: the suite used to
shell out to oxi-gen, found through ``--oxi-gen-bin``,
``ONTOLOGY_SUITE_OXI_GEN_BIN``, a sibling ``../oxi-gen`` checkout or ``PATH``,
and produced nothing without one.

# Why a subprocess, when the engine is importable

``semops_system.tarka.Mapping.run_csv`` maps a CSV in-process, but it has no
equivalent of two options this suite exposes: ``--normalize`` (upper-case
the column names) and ``--test N`` (map only the first N rows).
Reimplementing them here would mean rewriting CSV files in the suite's own
idea of tarka's dialect -- quoting, a backslash escape character, records
that span lines -- which is exactly the kind of near-copy that drifts. The
command, ``semops map run``, has both, and gives oxi-gen's output for every
option the suite passes. Measured before the switch, oxi-gen against this
command on the same inputs: the same RDF graph for each of ``--ntriples``,
``--tab``, ``-d``, ``--no-header-row``, ``--normalize``, ``--test=3`` and
``--dedup=1000``, and on 25 real CSV + query pairs from this repository and
the testing repository.

The command is run as ``python -m semops_system`` with this interpreter, so
it is always the ``semops-system`` this environment locked -- never a
``semops`` binary that happens to be earlier on ``PATH`` at another version.
It costs about 200 ms of interpreter start-up per CSV, against about 30 ms
for oxi-gen; for the handful of CSVs a triplify run takes, that is not worth
a second code path. It also keeps a native crash in one job from taking the
whole run with it.

# What changed for a caller

* A CSV that cannot be opened is an error with a message. oxi-gen panicked
  and left an empty output file behind, which looked like a job that
  succeeded and produced nothing.
* A failed run leaves no output file: tarka writes beside the target and
  moves the file into place when the run is complete.
* ``BOUND(?column)`` is true when the cell has a value, as TARQL defines it.
  oxi-gen substituted cells into the query text first, and a query with
  ``FILTER(BOUND(?column))`` made it panic and leave an empty file; tarka
  maps the rows that have a value (6 of 7 on a CSV with one empty cell).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .discovery import TriplifyJob

#: The command every job runs, before its own arguments.
TARKA_COMMAND = (sys.executable, "-m", "semops_system", "map", "run")


def available() -> bool:
    """Whether ``semops-system`` is importable. It is a runtime dependency,
    so this is false only in a broken environment -- but a triplify stage
    should say so in one line rather than fail every job the same way."""
    return importlib.util.find_spec("semops_system") is not None


def engine_version() -> Optional[str]:
    """The ``semops-system`` version that will run the jobs, for reports."""
    try:
        from importlib.metadata import version

        return version("semops-system")
    except Exception:  # noqa: BLE001 -- a report detail, never a reason to fail
        return None


class TriplifyError(RuntimeError):
    """One CSV + query pair did not triplify. Carries tarka's message."""

    def __init__(self, job: TriplifyJob, returncode: int, stderr: str):
        self.job = job
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"tarka exited {returncode} for {job.csv_path.name} + {job.query_path.name}: {stderr.strip()}"
        )


@dataclass
class TriplifyResult:
    job: TriplifyJob
    output_path: Path
    stdout: str
    stderr: str


def run_tarka(
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
    """Run one CSV + query pair through tarka, writing Turtle (or N-Triples,
    with ``ntriples=True``) to ``output_path``.

    The options are oxi-gen's, passed to ``semops map run`` under the same
    names. ``extra_args`` are passed through after them, for any other
    option of that command (``semops map run --help``).

    Raises ``TriplifyError``, with tarka's message attached, if the run
    fails, so a bad query or an unreadable CSV is a clear stage failure
    rather than a silently empty output file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        *TARKA_COMMAND,
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

    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise TriplifyError(job, proc.returncode, proc.stderr)
    return TriplifyResult(job=job, output_path=output_path, stdout=proc.stdout, stderr=proc.stderr)


def run_tarka_batch(
    jobs: List[TriplifyJob],
    out_dir: str | Path,
    **run_kwargs,
) -> tuple[List[TriplifyResult], List[TriplifyError]]:
    """Run every job in ``jobs`` through tarka, writing ``<csv-stem>.ttl``
    (or ``.nt``) into ``out_dir`` for each. Failures are collected rather
    than raised, so one bad CSV/query pair does not abort a whole batch --
    check the returned error list."""
    out_dir = Path(out_dir)
    suffix = ".nt" if run_kwargs.get("ntriples") else ".ttl"
    results: List[TriplifyResult] = []
    errors: List[TriplifyError] = []
    for job in jobs:
        output_path = out_dir / f"{job.csv_path.stem}{suffix}"
        try:
            results.append(run_tarka(job, output_path, **run_kwargs))
        except TriplifyError as exc:
            errors.append(exc)
    return results, errors
