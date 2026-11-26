"""Always-available reasoning backend: materializes an RDFS/OWL2-RL closure
via the ``owlrl`` package, then reruns this suite's own pattern-based
contradiction checks against the closure -- so entailed contradictions
surface (e.g. an individual's disjoint-class membership only implied via a
subclass chain), not just directly-asserted ones.

This is sound (every finding is a genuine entailment) but not complete for
full OWL2 DL: owlrl implements the OWL2 RL rule set, which is deliberately a
tractable fragment (see ``reasoning/profile.py``) -- a class can be genuinely
unsatisfiable in full OWL2 DL without any RL rule ever firing. Pair with
``external_backend.py`` for a real DL reasoner when that matters.

WHY THIS IS STILL ``owlrl`` AND NOT SEMOPS
------------------------------------------
Everything else in this suite has moved onto ``semops-system``: SPARQL to
HOLOS, SHACL to the SHACL engine, tabular input to tarka. This backend has
not, because there is nothing to move it to yet.

HOLOS's own design documents RDFS and OWL-RL profiles in its rule engine, so
this looks like it should already be available. Checked against the installed
``semops-system`` 0.31.1 rather than taken on trust: the SHACL engine's
``inference`` parameter accepts ``none``, ``rdfs``, ``rules`` and
``rules-iterated``, and anything else raises ``ValueError: unknown
inference``. There is no ``owlrl`` symbol anywhere in the package.

``rules`` is not a substitute. It materialises SHACL-AF ``sh:rule`` -- it runs
whatever rules a shapes graph happens to declare -- which is a different thing
from entailing the OWL-RL rule set over an arbitrary ontology. ``rdfs`` is a
real but much weaker closure: it would lose every finding that depends on an
OWL axiom, which is most of what this backend exists to catch.

So this stays on ``owlrl`` until SemOps exposes OWL-RL from Python, and the
gap is recorded in docs/ARCHITECTURE.md rather than left to be rediscovered.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import Graph

from ...checks.merge import ResultRow, build_unified_results
from ...checks.registry import Registry
from ...checks.sparql_runner import SparqlCheckOutcome, run_sparql_checks


def materialize_closure(graph: Graph) -> Graph:
    """Return a new graph containing ``graph``'s triples plus everything
    entailed by OWL2 RL rules (with RDFS closure folded in)."""
    closure = Graph()
    for prefix, ns in graph.namespaces():
        closure.bind(prefix, ns)
    for triple in graph:
        closure.add(triple)
    DeductiveClosure(
        OWLRL_Semantics, rdfs_closure=True, axiomatic_triples=False, datatype_axioms=False
    ).expand(closure)
    return closure


def run_owlrl_checks(
    graph: Graph, registry: Registry, sparql_dirs: List[str | Path]
) -> Tuple[List[ResultRow], List[SparqlCheckOutcome]]:
    """Materialize the owlrl closure of ``graph``, then run every ``.rq``
    check under each of ``sparql_dirs`` (typically ``sparql/logical`` and
    ``sparql/reasoning``) against the closure.

    Returns unified ``ResultRow``s (tagged with source ``"owlrl-closure"``)
    plus the raw per-check outcomes (so a query that fails to execute against
    the closure -- e.g. one using a construct the closure's extra triples
    confuse -- is still visible to the caller).
    """
    closure = materialize_closure(graph)

    merged_results = Graph()
    all_outcomes: List[SparqlCheckOutcome] = []
    for sparql_dir in sparql_dirs:
        if not Path(sparql_dir).is_dir():
            continue
        results, outcomes = run_sparql_checks(closure, sparql_dir)
        for triple in results:
            merged_results.add(triple)
        all_outcomes.extend(outcomes)

    rows = build_unified_results(Graph(), merged_results, registry, None)
    for row in rows:
        row.sources = sorted(set(row.sources) | {"owlrl-closure"})
    return rows, all_outcomes
