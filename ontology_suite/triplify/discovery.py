"""Pairs CSV input files with the oxi-gen/tarql CONSTRUCT query that should
triplify them, by filename convention -- the same convention
``tarql_visualiser.py`` uses to discover query files in a folder.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DEFAULT_CSV_GLOBS = "*.csv,*.tsv"
DEFAULT_QUERY_GLOBS = "*.sparql,*.rq,*.tarql,*.tq"


@dataclass
class TriplifyJob:
    csv_path: Path
    query_path: Path


def _glob_many(directory: Path, comma_globs: str) -> List[Path]:
    found = []
    for pattern in comma_globs.split(","):
        found.extend(directory.glob(pattern.strip()))
    return sorted(set(found))


def discover_jobs(
    csv_dir: str | Path,
    queries_dir: str | Path,
    csv_pattern: str = DEFAULT_CSV_GLOBS,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
) -> List[TriplifyJob]:
    """Pair every CSV in ``csv_dir`` with a query in ``queries_dir``.

    Pairing rule: a CSV named ``foo.csv`` pairs with a query file whose stem
    is ``foo`` (e.g. ``foo.sparql``), if one exists. If exactly one query
    file exists in ``queries_dir`` and no stem-matched query is found for a
    given CSV, that single query is used for every unmatched CSV (the common
    case of one CONSTRUCT query applied to every batch of a dataset). A CSV
    with no matching query and more than one candidate query available is
    skipped and reported via ``unmatched_csv_files`` on the return value's
    companion warnings -- call ``discover_jobs_verbose`` if you need those.
    """
    return discover_jobs_verbose(csv_dir, queries_dir, csv_pattern, query_pattern)[0]


def discover_jobs_verbose(
    csv_dir: str | Path,
    queries_dir: str | Path,
    csv_pattern: str = DEFAULT_CSV_GLOBS,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
) -> tuple[List[TriplifyJob], List[str]]:
    csv_dir = Path(csv_dir)
    queries_dir = Path(queries_dir)

    csv_files = _glob_many(csv_dir, csv_pattern)
    query_files = _glob_many(queries_dir, query_pattern)
    queries_by_stem = {q.stem: q for q in query_files}

    jobs: List[TriplifyJob] = []
    warnings: List[str] = []
    fallback_query: Optional[Path] = query_files[0] if len(query_files) == 1 else None

    for csv_path in csv_files:
        query_path = queries_by_stem.get(csv_path.stem)
        if query_path is None:
            if fallback_query is not None:
                query_path = fallback_query
            else:
                warnings.append(
                    f"No matching query for {csv_path.name} (no query named '{csv_path.stem}.*' "
                    f"and {len(query_files)} candidate queries in {queries_dir}, so no single fallback applies)."
                )
                continue
        jobs.append(TriplifyJob(csv_path=csv_path, query_path=query_path))

    return jobs, warnings
