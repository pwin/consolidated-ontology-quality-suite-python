"""
Renders a handful of charts from the unified result set. Kept deliberately
simple (matplotlib, no seaborn dependency) so the framework installs with a
small dependency footprint.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import matplotlib

matplotlib.use("Agg")  # headless rendering
import matplotlib.pyplot as plt
import pandas as pd

from ..checks.merge import ResultRow
from .tables import rows_to_dataframe, summary_by_category, top_offenders

SEVERITY_COLORS = {"Violation": "#c0392b", "Warning": "#e0a020", "Info": "#3f8fd6"}


def plot_severity_distribution(df: pd.DataFrame, out_path: str | Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    if df.empty:
        ax.text(0.5, 0.5, "No findings", ha="center", va="center")
        ax.axis("off")
    else:
        counts = df["severity"].value_counts().reindex(["Violation", "Warning", "Info"]).fillna(0)
        colors = [SEVERITY_COLORS[s] for s in counts.index]
        ax.bar(counts.index, counts.values, color=colors)
        ax.set_ylabel("Findings")
        ax.set_title("Findings by severity")
        for i, v in enumerate(counts.values):
            ax.text(i, v, str(int(v)), ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_category_breakdown(df: pd.DataFrame, out_path: str | Path) -> None:
    cat = summary_by_category(df)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    if cat.empty:
        ax.text(0.5, 0.5, "No findings", ha="center", va="center")
        ax.axis("off")
    else:
        bottom = None
        for sev in ["Violation", "Warning", "Info"]:
            values = cat[sev] if sev in cat.columns else [0] * len(cat)
            ax.bar(cat["category"], values, bottom=bottom, label=sev, color=SEVERITY_COLORS[sev])
            bottom = values if bottom is None else bottom + values
        ax.set_ylabel("Findings")
        ax.set_title("Findings by category (stacked by severity)")
        ax.legend()
        plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_top_offenders(df: pd.DataFrame, out_path: str | Path, n: int = 10) -> None:
    top = top_offenders(df, n=n)
    fig, ax = plt.subplots(figsize=(7, max(3, 0.4 * len(top))))
    if top.empty:
        ax.text(0.5, 0.5, "No findings", ha="center", va="center")
        ax.axis("off")
    else:
        top = top.iloc[::-1]  # largest at top of horizontal bar chart
        labels = [n.split("#")[-1].split("/")[-1] for n in top["focus_node"]]
        ax.barh(labels, top["findings"], color="#555555")
        ax.set_xlabel("Findings")
        ax.set_title(f"Top {len(top)} focus nodes by finding count")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def write_all_plots(rows: List[ResultRow], out_dir: str | Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = rows_to_dataframe(rows)
    plot_severity_distribution(df, out_dir / "severity_distribution.png")
    plot_category_breakdown(df, out_dir / "category_breakdown.png")
    plot_top_offenders(df, out_dir / "top_offenders.png")
