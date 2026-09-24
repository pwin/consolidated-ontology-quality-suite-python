"""
Tabular views of the unified result set.

`rows_to_dataframe` is the complete record, and `write_all_tables` puts it on
disk as `full_results.csv` -- the one file here that cannot be derived from
another, and the only one this package now writes.

The three aggregates -- `summary_by_category`, `summary_by_check` and
`top_offenders` -- are computed for `html_report` and `plots` to render. They
are no longer written as files of their own; see `write_all_tables` for what
was dropped and why.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from ..checks.merge import ResultRow
from ..checks.registry import Registry


_FULL_RESULTS_COLUMNS = [
    "check_id", "category", "title", "severity", "focus_node", "path", "value",
    "message", "remediation", "sources", "source_file", "line",
]


def rows_to_dataframe(rows: List[ResultRow]) -> pd.DataFrame:
    # Explicit columns even when rows is empty -- a plain pd.DataFrame([])
    # has none at all, so a fully clean run's full_results.csv comes out
    # completely empty (no header row), which pandas' own pd.read_csv can't
    # parse back (EmptyDataError) -- caught building a notebook that calls
    # pd.read_csv(full_results.csv) unconditionally after every run,
    # including a clean one.
    if not rows:
        return pd.DataFrame(columns=_FULL_RESULTS_COLUMNS)
    return pd.DataFrame(
        [
            {
                "check_id": r.check_id or "UNMAPPED",
                "category": r.category or "unmapped",
                "title": r.title or "",
                "severity": r.severity,
                "focus_node": r.focus_node,
                "path": r.path or "",
                "value": r.value or "",
                "message": r.message,
                "remediation": r.remediation or "",
                "sources": "+".join(r.sources),
                # Empty, not 0 or "unknown", when the finding could not be
                # placed: a reader filtering on `line` must not have to know
                # which sentinel means "no line". See checks/locate.py.
                "source_file": r.source_file or "",
                "line": r.line if r.line is not None else "",
            }
            for r in rows
        ]
    )


def summary_by_category(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["category", "Violation", "Warning", "Info", "total"])
    pivot = (
        df.pivot_table(index="category", columns="severity", values="check_id", aggfunc="count", fill_value=0)
        .reindex(columns=["Violation", "Warning", "Info"], fill_value=0)
    )
    pivot["total"] = pivot.sum(axis=1)
    return pivot.reset_index().sort_values("total", ascending=False)


def summary_by_check(df: pd.DataFrame, registry: Registry) -> pd.DataFrame:
    if df.empty:
        base = pd.DataFrame(
            [
                {
                    "check_id": cid,
                    "category": registry.get(cid).category,
                    "title": registry.get(cid).title,
                    "default_severity": registry.get(cid).default_severity,
                    "findings": 0,
                }
                for cid in registry.all_ids()
            ]
        )
        return base
    counts = df.groupby("check_id").size().rename("findings").reset_index()
    rows = []
    for cid in registry.all_ids():
        check = registry.get(cid)
        found = counts[counts["check_id"] == cid]["findings"]
        rows.append(
            {
                "check_id": cid,
                "category": check.category,
                "title": check.title,
                "default_severity": check.default_severity,
                "findings": int(found.iloc[0]) if len(found) else 0,
            }
        )
    result = pd.DataFrame(rows).sort_values(["findings", "check_id"], ascending=[False, True])
    return result


def top_offenders(df: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["focus_node", "findings"])
    counts = df.groupby("focus_node").size().rename("findings").reset_index()
    return counts.sort_values("findings", ascending=False).head(n)


def write_all_tables(rows: List[ResultRow], registry: Registry, out_dir: str | Path) -> None:
    """Writes the one table that is not derivable from another.

    This used to write seven files: `full_results.csv`, and then each of the
    three summaries as both a `.csv` and a `.md` -- the same numbers in two
    syntaxes, three times over, plus `top_offenders` a third time as a PNG.
    Every one of the six was a `groupby` over `full_results.csv`, and a search
    of this repo, its three notebooks and its docs found not one reader of any
    of them. They were written on every run of every subcommand, by nobody's
    request, and the cost was not the disk: it was that an out/ directory of
    thirteen files gives no clue which one to open.

    The aggregates themselves are not gone and were never the duplication.
    `summary_by_category`, `summary_by_check` and `top_offenders` are still
    here and still called -- by `html_report`, which renders them as tables,
    and by `plots`, which draws them. One computation, rendered where someone
    reads it, instead of six files nobody opened.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_to_dataframe(rows).to_csv(out_dir / "full_results.csv", index=False)
