"""
Runs the standalone SPARQL CONSTRUCT tests under sparql/**/*.rq.

Each query is fully self-contained and produces standard
``sh:ValidationResult`` triples. This is the "portable" execution path:
it needs nothing but a SPARQL 1.1 engine (rdflib here, oxigraph in the
Rust framework) and no SHACL processor at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from rdflib import Graph


@dataclass
class SparqlCheckOutcome:
    check_id: str
    file: str
    ok: bool
    error: str | None
    result_count: int


def discover_queries(sparql_dir: str | Path) -> List[Path]:
    sparql_dir = Path(sparql_dir)
    return sorted(sparql_dir.rglob("*.rq"))


def run_sparql_checks(graph: Graph, sparql_dir: str | Path) -> tuple[Graph, List[SparqlCheckOutcome]]:
    """Run every .rq CONSTRUCT query in sparql_dir against `graph`.

    Returns a merged results graph plus a per-check outcome list (useful for
    surfacing queries that failed to execute, e.g. due to an engine that
    does not support a SPARQL 1.1 feature used in one of the checks).
    """
    results = Graph()
    outcomes: List[SparqlCheckOutcome] = []

    for path in discover_queries(sparql_dir):
        check_id = path.stem
        query_text = path.read_text(encoding="utf-8")
        try:
            qres = graph.query(query_text)
            count = 0
            for triple in qres.graph if qres.graph is not None else []:
                results.add(triple)
                count += 1
            # rdflib CONSTRUCT results exposes .graph; guard for older versions
            if qres.type == "CONSTRUCT" and qres.graph is None:
                for row in qres:
                    results.add(row)
                    count += 1
            outcomes.append(SparqlCheckOutcome(check_id, str(path), True, None, count))
        except Exception as exc:  # noqa: BLE001 - we want to keep going on any error
            outcomes.append(SparqlCheckOutcome(check_id, str(path), False, str(exc), 0))

    return results, outcomes
