"""
Renders the unified result set as:

  * a standard Cucumber JSON document (readable by any Cucumber-compatible
    viewer / CI plugin), grouping checks into Features (by category) and
    Scenarios (by check id);
  * a companion human-readable Gherkin ``.feature`` text file, since raw
    Cucumber JSON is awkward for a human (or an LLM) to skim directly.

Status mapping (Cucumber JSON only supports passed/failed/skipped/pending/
undefined, it has no native "warning" concept):

  * no results for the check                    -> passed
  * results exist, but the worst severity found
    is Warning or Info                          -> pending  (attention
                                                    needed, not blocking)
  * at least one Violation-severity result       -> failed
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

from ..checks.merge import ResultRow
from ..checks.registry import Registry

_WORST = {"Violation": 0, "Warning": 1, "Info": 2}


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")


def _group_by_check(rows: List[ResultRow]) -> Dict[str, List[ResultRow]]:
    grouped: Dict[str, List[ResultRow]] = {}
    for row in rows:
        key = row.check_id or "UNMAPPED"
        grouped.setdefault(key, []).append(row)
    return grouped


def build_cucumber_document(rows: List[ResultRow], registry: Registry) -> list:
    grouped = _group_by_check(rows)
    by_category: Dict[str, list] = {}
    for check_id in registry.all_ids():
        check = registry.get(check_id)
        by_category.setdefault(check.category, []).append(check_id)

    features = []
    for category in registry.categories():
        check_ids = by_category.get(category, [])
        if not check_ids:
            continue
        feature_name = registry.get(check_ids[0]).cucumber_feature
        elements = []
        for check_id in check_ids:
            check = registry.get(check_id)
            findings = grouped.get(check_id, [])
            if not findings:
                status = "passed"
                error_message = None
            else:
                worst = min(_WORST.get(r.severity, 2) for r in findings)
                status = "failed" if worst == 0 else "pending"
                sample = findings[:5]
                lines = [
                    f"{f.severity}: {f.focus_node} -- {f.message}" for f in sample
                ]
                more = len(findings) - len(sample)
                if more > 0:
                    lines.append(f"... and {more} more")
                error_message = (
                    f"{len(findings)} finding(s) for {check_id} ({check.title}):\n"
                    + "\n".join(lines)
                    + f"\n\nRemediation: {check.remediation}"
                )

            step_result = {"status": status}
            if error_message:
                step_result["error_message"] = error_message

            elements.append(
                {
                    "id": f"{_slug(feature_name)};{_slug(check.cucumber_scenario)}",
                    "keyword": "Scenario",
                    "type": "scenario",
                    "name": f"[{check_id}] {check.cucumber_scenario}",
                    "description": check.description,
                    "tags": [
                        {"name": f"@{check.category}"},
                        {"name": f"@{check.default_severity.lower()}"},
                        {"name": f"@{check_id}"},
                    ],
                    "steps": [
                        {
                            "keyword": "Given ",
                            "name": "the combined ontology and data graph",
                            "result": {"status": "passed"},
                        },
                        {
                            "keyword": "Then ",
                            "name": check.cucumber_scenario,
                            "result": step_result,
                        },
                    ],
                }
            )

        features.append(
            {
                "id": _slug(feature_name),
                "uri": f"features/{_slug(feature_name)}.feature",
                "keyword": "Feature",
                "name": feature_name,
                "elements": elements,
            }
        )

    return features


def write_cucumber_json(rows: List[ResultRow], registry: Registry, out_path: str | Path) -> None:
    doc = build_cucumber_document(rows, registry)
    Path(out_path).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def write_gherkin_feature_files(rows: List[ResultRow], registry: Registry, out_dir: str | Path) -> None:
    """Write one human-readable .feature file per category, mirroring the
    Cucumber JSON content but in plain Gherkin text for quick reading."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    grouped = _group_by_check(rows)

    by_category: Dict[str, list] = {}
    for check_id in registry.all_ids():
        check = registry.get(check_id)
        by_category.setdefault(check.category, []).append(check_id)

    for category, check_ids in by_category.items():
        feature_name = registry.get(check_ids[0]).cucumber_feature
        lines = [f"Feature: {feature_name}", ""]
        for check_id in check_ids:
            check = registry.get(check_id)
            findings = grouped.get(check_id, [])
            worst = min((_WORST.get(r.severity, 2) for r in findings), default=None)
            if worst is None:
                status = "PASSED"
            elif worst == 0:
                status = "FAILED"
            else:
                status = "NEEDS ATTENTION"

            lines.append(f"  @{check.category} @{check.default_severity.lower()} @{check_id}")
            lines.append(f"  Scenario: [{check_id}] {check.cucumber_scenario}  # {status}")
            lines.append("    Given the combined ontology and data graph")
            lines.append(f"    Then {check.cucumber_scenario}")
            if findings:
                lines.append(f"    # {len(findings)} finding(s):")
                for f in findings[:5]:
                    lines.append(f"    #   - {f.severity}: {f.focus_node} -- {f.message}")
                if len(findings) > 5:
                    lines.append(f"    #   ... and {len(findings) - 5} more")
                lines.append(f"    # Remediation: {check.remediation}")
            lines.append("")

        path = out_dir / f"{_slug(feature_name)}.feature"
        path.write_text("\n".join(lines), encoding="utf-8")
