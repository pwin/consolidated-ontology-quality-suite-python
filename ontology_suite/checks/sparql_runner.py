"""
Runs the standalone SPARQL CONSTRUCT tests under sparql/**/*.rq.

Each query is fully self-contained and produces standard
``sh:ValidationResult`` triples. This is the "portable" execution path: it
needs nothing but a SPARQL engine and no SHACL processor at all.

The engine is holosdb -- see `holos_sparql.py` for why it is not rdflib, what
that measurably fixed, and the one thing it changes in the output. rdflib is
still what parses the input and holds the results.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Union

from rdflib import Graph

from .holos_sparql import HolosSession, QueryFailed

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
    does not support a SPARQL feature used in one of the checks).

    The graph is transferred into one holosdb store up front and every check
    queries that store, so the cost of crossing between the two libraries is
    paid once per run rather than once per check.
    """
    results = Graph()
    outcomes: List[SparqlCheckOutcome] = []

    queries = discover_queries(sparql_dirs)
    if not queries:
        # No store, because building one means serialising the whole graph for
        # nothing. A caller pointing at an empty tree is a real case: project
        # roots are passed alongside the suite's own.
        return results, outcomes

    with HolosSession(graph) as session:
        for path in queries:
            check_id = path.stem
            query_text = path.read_text(encoding="utf-8")
            try:
                constructed = session.construct(query_text)
            except QueryFailed as exc:
                outcomes.append(SparqlCheckOutcome(check_id, str(path), False, str(exc), 0))
                continue
            count = 0
            for triple in constructed:
                results.add(triple)
                count += 1
            outcomes.append(SparqlCheckOutcome(check_id, str(path), True, None, count))

    return results, outcomes
