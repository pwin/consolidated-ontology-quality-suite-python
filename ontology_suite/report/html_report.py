"""
Builds a single, self-contained HTML dashboard: severity/category counts,
plots (embedded as base64 so the file is portable on its own), the full
findings table grouped by category/check, and remediation guidance --
meant to be the first thing a human (or an AI agent) opens to triage
results.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import List, Optional, Tuple

from .. import config
from ..checks.merge import ResultRow
from ..checks.registry import Registry
from .tables import rows_to_dataframe, summary_by_category, summary_by_check, top_offenders

SEVERITY_BADGE = {
    "Violation": "background:#c0392b;color:#fff;",
    "Warning": "background:#e0a020;color:#212121;",
    "Info": "background:#3f8fd6;color:#fff;",
}

CSS = """
:root { --bg:#0f1115; --panel:#181b21; --border:#2a2e37; --text:#e8e9ec; --muted:#9aa0ab; --accent:#5fb3ff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 1.6rem; margin-bottom: 4px; }
.subtitle { color: var(--muted); margin-top:0; margin-bottom: 28px; font-size: 0.95rem; }
.cards { display:flex; gap:16px; margin-bottom: 28px; flex-wrap:wrap; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px 20px; min-width:140px; }
.card .n { font-size:1.8rem; font-weight:700; }
.card .l { color:var(--muted); font-size:0.8rem; text-transform:uppercase; letter-spacing:0.04em; }
section { margin-bottom: 40px; }
h2 { font-size: 1.15rem; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
table { width:100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 12px; }
th, td { text-align:left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight:600; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.03em; }
tr:hover { background: #1d212a; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.72rem; font-weight:600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.8rem; word-break:break-all; }
img.plot { max-width: 100%; border-radius: 8px; border:1px solid var(--border); }
.plots { display:flex; gap:16px; flex-wrap:wrap; }
.plots figure { margin:0; flex: 1 1 300px; }
figcaption { color: var(--muted); font-size:0.8rem; margin-top:6px; }
.remediation { color: var(--muted); font-size: 0.82rem; }
a { color: var(--accent); }
"""


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _badge(sev: str) -> str:
    return f'<span class="badge" style="{SEVERITY_BADGE.get(sev, "")}">{sev}</span>'


def write_html_report(
    rows: List[ResultRow],
    registry: Registry,
    plots_dir: str | Path,
    out_path: str | Path,
    title: str = "Ontology & Data Quality Report",
    artifacts: Optional[List[Tuple[str, Path]]] = None,
) -> None:
    plots_dir = Path(plots_dir)
    df = rows_to_dataframe(rows)
    cat = summary_by_category(df)
    chk = summary_by_check(df, registry)
    top = top_offenders(df)

    n_violation = int((df["severity"] == "Violation").sum()) if not df.empty else 0
    n_warning = int((df["severity"] == "Warning").sum()) if not df.empty else 0
    n_info = int((df["severity"] == "Info").sum()) if not df.empty else 0

    plot_imgs = ""
    for fname, caption in [
        ("severity_distribution.png", "Findings by severity"),
        ("category_breakdown.png", "Findings by category, stacked by severity"),
        ("top_offenders.png", "Focus nodes with the most findings"),
    ]:
        p = plots_dir / fname
        if p.exists():
            plot_imgs += (
                f'<figure><img class="plot" src="data:image/png;base64,{_b64(p)}">'
                f"<figcaption>{caption}</figcaption></figure>"
            )

    cat_rows = "".join(
        f"<tr><td>{r.category}</td><td>{int(r.Violation)}</td>"
        f"<td>{int(r.Warning)}</td><td>{int(r.Info)}</td>"
        f"<td><strong>{int(r.total)}</strong></td></tr>"
        for r in cat.itertuples()
    )

    chk_rows = "".join(
        f"<tr><td class='mono'>{r.check_id}</td><td>{r.category}</td><td>{r.title}</td>"
        f"<td>{_badge(r.default_severity)}</td><td>{r.findings}</td></tr>"
        for r in chk.itertuples()
    )

    top_rows = "".join(
        f"<tr><td class='mono'>{r.focus_node}</td><td>{r.findings}</td></tr>"
        for r in top.itertuples()
    )

    artifacts_html = ""
    if artifacts:
        artifact_rows = ""
        for label, path in artifacts:
            path = Path(path)
            is_turtle = path.suffix in (".ttl", ".turtle", ".nt")
            hint = (
                f" -- open in <a href=\"{config.TURTLE_EDITOR_VIEWER_URL}\">turtle-editor-viewer</a> "
                "(paste in or load this file) to browse the graph and run SPARQL queries against it interactively"
                if is_turtle else ""
            )
            artifact_rows += f"<tr><td>{label}</td><td class='mono'>{path}</td><td>{hint}</td></tr>"
        artifacts_html = f"""
  <section>
    <h2>Artifacts</h2>
    <table>
      <thead><tr><th>Stage output</th><th>Path</th><th></th></tr></thead>
      <tbody>{artifact_rows}</tbody>
    </table>
  </section>"""

    finding_rows = ""
    for r in rows[:500]:  # cap to keep the file a sane size; full data is in full_results.csv
        finding_rows += (
            "<tr>"
            f"<td class='mono'>{r.check_id or 'UNMAPPED'}</td>"
            f"<td>{_badge(r.severity)}</td>"
            f"<td class='mono'>{r.focus_node}</td>"
            f"<td class='mono'>{r.path or ''}</td>"
            f"<td>{r.message}</td>"
            f"<td class='remediation'>{r.remediation or ''}</td>"
            f"<td>{'+'.join(r.sources)}</td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <p class="subtitle">Combined SHACL + SPARQL validation results, unified as standard <code>sh:ValidationResult</code> rows.</p>

  <div class="cards">
    <div class="card"><div class="n">{len(rows)}</div><div class="l">Total findings</div></div>
    <div class="card"><div class="n">{n_violation}</div><div class="l">Violations</div></div>
    <div class="card"><div class="n">{n_warning}</div><div class="l">Warnings</div></div>
    <div class="card"><div class="n">{n_info}</div><div class="l">Info</div></div>
  </div>

  {artifacts_html}

  <section>
    <h2>Charts</h2>
    <div class="plots">{plot_imgs}</div>
  </section>

  <section>
    <h2>By category</h2>
    <table>
      <thead><tr><th>Category</th><th>Violation</th><th>Warning</th><th>Info</th><th>Total</th></tr></thead>
      <tbody>{cat_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>By check</h2>
    <table>
      <thead><tr><th>ID</th><th>Category</th><th>Title</th><th>Default severity</th><th>Findings</th></tr></thead>
      <tbody>{chk_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Top offending nodes</h2>
    <table>
      <thead><tr><th>Focus node</th><th>Findings</th></tr></thead>
      <tbody>{top_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Findings (first 500)</h2>
    <table>
      <thead><tr><th>Check</th><th>Severity</th><th>Focus node</th><th>Path</th><th>Message</th><th>Remediation</th><th>Engine(s)</th></tr></thead>
      <tbody>{finding_rows}</tbody>
    </table>
  </section>
</div>
</body>
</html>"""

    Path(out_path).write_text(html, encoding="utf-8")
