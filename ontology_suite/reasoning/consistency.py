"""Dispatches OWL2 logical-validity / consistency checking to whichever
backend(s) apply, and returns a single unified ``ResultRow`` list so the
report layer never needs to know which backend produced a finding.

``owlrl_backend`` always runs (pure Python, no external process). The
external DL-reasoner backend is additionally attempted unless explicitly
disabled; when it isn't available (or fails), a ``REA-022`` informational
row records that only the sound-but-incomplete owlrl checks ran, rather than
silently saying nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from rdflib import Graph

from ..checks.merge import ResultRow
from ..checks.registry import Registry
from .backends import external_backend, owlrl_backend

REASONER_CHOICES = ("auto", "owlrl-only", "hermit", "pellet", "none")


def run_consistency_checks(
    graph: Graph,
    registry: Registry,
    sparql_root: str | Path,
    reasoner: str = "auto",
) -> List[ResultRow]:
    """Run the reasoning/consistency layer over ``graph``.

    ``reasoner``:
      - ``"auto"``       -- owlrl always, plus HermiT via owlready2 if available
      - ``"owlrl-only"``  -- owlrl only, skip the external-reasoner attempt entirely
      - ``"hermit"`` / ``"pellet"`` -- owlrl plus that specific external reasoner
      - ``"none"``        -- skip all reasoning (registry/SPARQL checks elsewhere are unaffected)
    """
    if reasoner not in REASONER_CHOICES:
        raise ValueError(f"Unknown reasoner {reasoner!r}; choose one of {REASONER_CHOICES}")

    sparql_root = Path(sparql_root)
    rows: List[ResultRow] = []

    if reasoner == "none":
        return rows

    # Only sparql/logical/closure-safe/*.rq (not the whole logical/ tree) --
    # LOG-003/006/007 describe an *authored-axiom* property (a redundant
    # equivalentClass+subClassOf pair; a symmetric/transitive property's own
    # declared domain/range) rather than a genuine contradiction. Rerunning
    # them against the owlrl closure produces false positives: OWL2 RL
    # entails the reciprocal subClassOf from every equivalentClass axiom
    # (making LOG-003 fire on every one, authored redundantly or not), and
    # RDFS's subPropertyOf domain/range propagation can entail a mismatched
    # domain/range onto a property that never declared either directly
    # (LOG-006/007). Caught as a real bug against a real vehicle ontology
    # importing gist 14.1.0: LOG-003 went from 0 genuinely-redundant pairs
    # to 164 spurious findings (all 59 of the ontology's equivalentClass
    # axioms, none authored redundantly) purely from this. See
    # docs/REASONING.md.
    owlrl_rows, _outcomes = owlrl_backend.run_owlrl_checks(
        graph, registry, [sparql_root / "logical" / "closure-safe", sparql_root / "reasoning"]
    )
    rows.extend(owlrl_rows)

    if reasoner == "owlrl-only":
        return rows

    backend_name = "pellet" if reasoner == "pellet" else "hermit"
    external_rows = external_backend.run_external_reasoner(graph, reasoner=backend_name)
    if external_rows is None:
        rows.append(external_backend.unavailable_row(backend_name, "owlready2 is not installed"))
    else:
        rows.extend(external_rows)

    return rows
