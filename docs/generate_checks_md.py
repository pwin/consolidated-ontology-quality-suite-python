"""Regenerates docs/CHECKS.md from ontology_suite/resources/registry.json.

Run after adding/editing any check:  python docs/generate_checks_md.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from ontology_suite import config  # noqa: E402

REGISTRY_PATH = config.DEFAULT_REGISTRY_PATH
OUT_PATH = REPO_ROOT / "docs" / "CHECKS.md"

CATEGORY_TITLES = {
    "structural": "Structural integrity",
    "logical": "Logical cogency",
    "quality": "Quality / documentation",
    "efficiency": "Structural & runtime efficiency",
    "style": "Naming style",
    "data": "Data quality",
    "reasoning": "Reasoning (OWL2 profile & consistency)",
    "conformance": "Ontology conformance (data/sketch vs. declarations)",
}


def main() -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_category = defaultdict(list)
    for check in registry["checks"]:
        by_category[check["category"]].append(check)

    lines = [
        "# Check catalogue",
        "",
        f"Generated from `registry.json` by `docs/generate_checks_md.py` -- "
        f"{len(registry['checks'])} checks across {len(by_category)} categories. "
        "Do not hand-edit; re-run the generator instead.",
        "",
    ]

    for category, checks in sorted(by_category.items(), key=lambda kv: CATEGORY_TITLES.get(kv[0], kv[0])):
        title = CATEGORY_TITLES.get(category, category.title())
        lines.append(f"## {title} (`{category}`)")
        lines.append("")
        for check in sorted(checks, key=lambda c: c["id"]):
            lines.append(f"### `{check['id']}` -- {check['title']}")
            lines.append("")
            lines.append(f"- **Default severity:** {check['default_severity']}")
            lines.append(f"- **Metric:** {check['metric']}")
            lines.append(f"- **Description:** {check['description']}")
            lines.append(f"- **Remediation:** {check['remediation']}")
            lines.append(f"- **Cucumber:** {check['cucumber_feature']} / {check['cucumber_scenario']}")
            lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(registry['checks'])} checks)")


if __name__ == "__main__":
    main()
