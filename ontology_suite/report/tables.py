"""
Builds tabular summaries of the unified result set: a full results table,
a per-check summary, a per-category summary, and a "top offenders" table
(the focus nodes with the most findings) -- the kind of views a human or
an AI agent needs to triage issues quickly.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from ..checks.merge import ResultRow
from ..checks.registry import Registry


_FULL_RESULTS_COLUMNS = [
    "check_id", "category", "title", "severity", "focus_node", "path", "value",
    "message", "remediation", "sources",
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
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = rows_to_dataframe(rows)
    df.to_csv(out_dir / "full_results.csv", index=False)

    cat = summary_by_category(df)
    cat.to_csv(out_dir / "summary_by_category.csv", index=False)
    (out_dir / "summary_by_category.md").write_text(
        cat.to_markdown(index=False) if not cat.empty else "_No results._", encoding="utf-8"
    )

    chk = summary_by_check(df, registry)
    chk.to_csv(out_dir / "summary_by_check.csv", index=False)
    (out_dir / "summary_by_check.md").write_text(chk.to_markdown(index=False), encoding="utf-8")

    top = top_offenders(df)
    top.to_csv(out_dir / "top_offenders.csv", index=False)
    (out_dir / "top_offenders.md").write_text(
        top.to_markdown(index=False) if not top.empty else "_No results._", encoding="utf-8"
    )
