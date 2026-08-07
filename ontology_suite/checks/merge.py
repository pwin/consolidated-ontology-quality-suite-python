"""
Combines the SHACL-native results graph and the standalone-SPARQL results
graph into one list of unified result rows, using ``registry.Registry`` to
resolve each result back to its check id, category, remediation text, etc.

Because several checks are implemented twice (once as a SHACL/SHACL-SPARQL
shape, once as a portable SPARQL CONSTRUCT query), the same real-world
violation can appear in both result graphs. This module deduplicates on
(check_id, focus_node, path, value) and records which engine(s) produced
each finding, which doubles as a regression check on the two formulations:
if a violation is only ever found by one engine, that is worth
investigating.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rdflib import Graph, URIRef
from rdflib.namespace import Namespace

from .registry import Registry

SH = Namespace("http://www.w3.org/ns/shacl#")

SEVERITY_ORDER = {
    "http://www.w3.org/ns/shacl#Violation": 0,
    "http://www.w3.org/ns/shacl#Warning": 1,
    "http://www.w3.org/ns/shacl#Info": 2,
}
SEVERITY_LABEL = {
    "http://www.w3.org/ns/shacl#Violation": "Violation",
    "http://www.w3.org/ns/shacl#Warning": "Warning",
    "http://www.w3.org/ns/shacl#Info": "Info",
}


@dataclass
class ResultRow:
    check_id: Optional[str]
    category: Optional[str]
    title: Optional[str]
    severity: str
    focus_node: str
    path: Optional[str]
    value: Optional[str]
    message: str
    remediation: Optional[str]
    sources: List[str] = field(default_factory=list)


def _extract_rows(
    results_graph: Graph,
    registry: Registry,
    shapes_graph: Optional[Graph],
    source_label: str,
) -> List[ResultRow]:
    rows: List[ResultRow] = []
    for result in results_graph.subjects(SH.resultSeverity, None):
        severity = results_graph.value(result, SH.resultSeverity)
        focus = results_graph.value(result, SH.focusNode)
        path = results_graph.value(result, SH.resultPath)
        value = results_graph.value(result, SH.value)
        message = results_graph.value(result, SH.resultMessage)
        scc = results_graph.value(result, SH.sourceConstraintComponent)
        shape = results_graph.value(result, SH.sourceShape)

        check_id = registry.resolve_check_id(scc, shape, shapes_graph)
        check = registry.get(check_id) if check_id else None

        # A check whose SPARQL CONSTRUCT never binds sh:value (common --
        # 18 of ~50 checks in this suite don't) leaves it unset; pyshacl,
        # for the matching sh:sparql SHACL formulation, defaults sh:value
        # to $this (the focus node) per the SHACL spec whenever its
        # sh:select query doesn't select its own ?value column. Without
        # normalizing the same way here, those two formulations of the
        # *same* finding produce different dedup keys below (value=None vs
        # value=<focus node>) and both survive as separate rows -- caught
        # as a real bug: STR-003 and QUA-001 each showed up with exactly
        # double their real finding count against a vehicle ontology
        # importing gist 14.1.0, purely from this mismatch, not from any
        # actual difference in what the two engines found.
        if value is None and focus is not None:
            value = focus

        rows.append(
            ResultRow(
                check_id=check_id,
                category=check.category if check else None,
                title=check.title if check else None,
                severity=SEVERITY_LABEL.get(str(severity), str(severity)),
                focus_node=str(focus) if focus is not None else "",
                path=str(path) if path is not None else None,
                value=str(value) if value is not None else None,
                message=str(message) if message is not None else "",
                remediation=check.remediation if check else None,
                sources=[source_label],
            )
        )
    return rows


def build_unified_results(
    shacl_results_graph: Graph,
    sparql_results_graph: Graph,
    registry: Registry,
    shapes_graph: Optional[Graph] = None,
) -> List[ResultRow]:
    shacl_rows = _extract_rows(shacl_results_graph, registry, shapes_graph, "shacl")
    sparql_rows = _extract_rows(sparql_results_graph, registry, None, "sparql")

    merged: Dict[tuple, ResultRow] = {}
    for row in shacl_rows + sparql_rows:
        key = (row.check_id, row.focus_node, row.path, row.value)
        if key in merged:
            merged[key].sources = sorted(set(merged[key].sources + row.sources))
        else:
            merged[key] = row

    # Stable ordering: severity, then category, then check id, then focus node
    def sort_key(r: ResultRow):
        return (
            SEVERITY_ORDER.get(f"http://www.w3.org/ns/shacl#{r.severity}", 9),
            r.category or "zzz",
            r.check_id or "zzz",
            r.focus_node,
        )

    return sorted(merged.values(), key=sort_key)
