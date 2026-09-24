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
from typing import List, Sequence, Union

from rdflib import Graph

#: One query tree, or several to compose.
QueryRoots = Union[str, Path, Sequence[Union[str, Path]]]


@dataclass
class SparqlCheckOutcome:
    check_id: str
    file: str
    ok: bool
    error: str | None
    result_count: int


# Checks that read a graph nobody else builds, and so must not be run against
# an ontology or a data graph. `sparql/tarql/` holds queries over the BIND
# facts graph (sketch/bind_analysis.py::bind_report_to_graph) -- a vocabulary
# an ontology never contains, so running them elsewhere costs a parse and
# matches nothing. Excluded by name rather than left to match nothing anyway,
# because "runs everywhere and is silent almost everywhere" is how a check
# that has quietly stopped working looks.
SUBJECT_SPECIFIC_DIRS = ("tarql",)


def as_dirs(sparql_dirs: QueryRoots) -> List[Path]:
    """One directory or several, as a list. A `str` is a path, not a sequence
    of characters, which is the bug this exists to make impossible."""
    if isinstance(sparql_dirs, (str, Path)):
        return [Path(sparql_dirs)]
    return [Path(d) for d in sparql_dirs]


def discover_queries(sparql_dirs: QueryRoots, include_subject_specific: bool = False) -> List[Path]:
    """Every `.rq` under `sparql_dirs`, minus the subject-specific ones.

    Takes one directory or several. Several is how a project runs its own
    checks *alongside* the suite's rather than instead of them: the flag used
    to take a single path, so `--sparql my-checks` silently replaced all 42
    built-in queries with however many the project had, and nothing said so.
    Measured on the testing repo's own CI gate, which was running 8 checks
    while its merged registry declared 61.

    Subject-specific filtering is per root, so pointing directly at a
    `tarql/` directory still runs it -- a caller that has built the BIND facts
    graph wants those queries and nothing else, which is what the sketch stage
    does. `include_subject_specific=True` keeps them wherever they appear.

    Deduplicated by resolved path, so overlapping roots -- the suite's tree
    passed alongside a project tree that copies part of it -- run each query
    once rather than reporting it twice.
    """
    seen: set = set()
    out: List[Path] = []
    for root in as_dirs(sparql_dirs):
        for path in sorted(root.rglob("*.rq")):
            if not include_subject_specific and (
                set(path.relative_to(root).parts[:-1]) & set(SUBJECT_SPECIFIC_DIRS)
            ):
                continue
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def run_sparql_checks(graph: Graph, sparql_dirs: QueryRoots) -> tuple[Graph, List[SparqlCheckOutcome]]:
    """Run every .rq CONSTRUCT query in `sparql_dirs` against `graph`.

    One directory or several; see `discover_queries`.

    Returns a merged results graph plus a per-check outcome list (useful for
    surfacing queries that failed to execute, e.g. due to an engine that
    does not support a SPARQL 1.1 feature used in one of the checks).
    """
    results = Graph()
    outcomes: List[SparqlCheckOutcome] = []

    for path in discover_queries(sparql_dirs):
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
