"""Guards the assumption `--engine sparql` (pipeline.ENGINE_CHOICES) relies
on for safety: every check in registry.json that has a SHACL shape also has
a portable SPARQL `.rq` twin, so running the SPARQL layer alone never
silently drops coverage pyshacl would otherwise have caught.

pyshacl is, in practice, the dominant cost of a registry-suite run (measured
at ~193s vs. ~27s for the same ~50-check pass over a real ~3,300-triple
ontology -- see docs/ARCHITECTURE.md) because it re-derives, via its own
Python-level shape traversal, findings the portable SPARQL layer already
produces directly. `--engine sparql` skips it entirely. That's only safe
because there is currently no check implemented in SHACL alone -- if a
future contributor ever adds one without a matching `.rq` file, this test
should fail and say exactly which check id is at risk.
"""
import json
import re
from pathlib import Path

from ontology_suite import config

REGISTRY = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
ALL_IDS = sorted(c["id"] for c in REGISTRY["checks"])


def _sparql_ids() -> set:
    return {f.stem for f in config.DEFAULT_SPARQL_DIR.rglob("*.rq")}


def _shacl_ids() -> set:
    """A check id is "in SHACL" if some shapes/*.ttl file either names a
    shape after it directly (oq:<id>) or annotates a shape with
    oq:checkId "<id>" -- the same two ways registry.py's resolve_check_id
    recognizes a SHACL result as belonging to that check."""
    found = set()
    for shapes_file in config.DEFAULT_SHAPES_DIR.glob("*.ttl"):
        text = shapes_file.read_text(encoding="utf-8")
        for check_id in ALL_IDS:
            if re.search(rf"oq:{re.escape(check_id)}\b", text) or re.search(
                rf'oq:checkId\s+"{re.escape(check_id)}"', text
            ):
                found.add(check_id)
    return found


def test_every_shacl_check_has_a_sparql_twin():
    shacl_only = sorted(_shacl_ids() - _sparql_ids())
    assert shacl_only == [], (
        f"{shacl_only} are implemented in SHACL with no portable SPARQL .rq twin -- "
        "`--engine sparql` would silently miss these; either add the missing .rq file(s) "
        "or don't rely on --engine sparql until they exist"
    )


def test_every_registered_check_is_implemented_somewhere():
    """Every id in registry.json should resolve to at least one real
    implementation (a .rq file, a SHACL shape, or a native-Python check
    like reasoning/profile.py's REA-01x or data_quality.py's CNF-*) -- an
    id with neither is dead registry metadata, not a bug in --engine
    selection, but still worth catching."""
    native_python_ids = {
        # reasoning/profile.py
        "REA-010", "REA-011", "REA-012",
        # reasoning/backends/external_backend.py
        "REA-020", "REA-021", "REA-022",
        # dataquality/data_quality.py::conformance_to_rows
        "CNF-001", "CNF-002", "CNF-003", "CNF-004", "CNF-005",
        # sketch/bind_analysis.py::bind_report_to_rows -- reads TARQL query
        # source. There is no graph for a .rq/shape to match against: the
        # sketch graph keeps only each query's CONSTRUCT template, so every
        # BIND expression is discarded before it exists.
        "TQL-001", "TQL-002", "TQL-003",
    }
    implemented = _sparql_ids() | _shacl_ids() | native_python_ids
    unimplemented = sorted(set(ALL_IDS) - implemented)
    assert unimplemented == [], f"{unimplemented} are registered but have no known implementation"


# ---------------------------------------------------------------------------
# The behavioral counterpart to the two static tests above: a `.rq` twin that
# *exists* but reports the same finding differently is not coverage. Each
# engine's rows go through the same `(check_id, focus_node, path, value)`
# dedup key, so a twin that omits an `sh:resultPath` or `sh:value` its shape
# does emit doesn't merge with it -- the identical finding survives as two
# rows, and `--engine both` silently over-counts relative to `--engine
# sparql`. Three checks had drifted this way (LOG-001 and LOG-003 emitted no
# `sh:resultPath`, STY-003 no `sh:value`), which is the entire 31-vs-28 gap
# this test was written against.
# ---------------------------------------------------------------------------
import pytest
import rdflib

from ontology_suite import pipeline
from ontology_suite.checks.registry import Registry

PARITY_FIXTURES = {
    "domain": ["examples/ontology/domain.ttl"],
    "property_axioms": ["examples/property_axioms/ontology.ttl"],
}


def _rows(paths, engine):
    graph = rdflib.Graph()
    for path in paths:
        graph.parse(config.REPO_ROOT / path, format="turtle")
    registry = Registry.load(config.DEFAULT_REGISTRY_PATH)
    return {
        (r.check_id, r.severity, r.focus_node, r.path or "", r.value or "")
        for r in pipeline.run_registry_suite_on_graph(graph, registry, engine=engine)
    }


@pytest.mark.parametrize("name", sorted(PARITY_FIXTURES))
def test_sparql_only_reports_exactly_what_both_engines_report(name):
    """`--engine sparql` must be a pure speed choice, not a different answer.

    Both directions matter. Rows only `both` has mean a shape reports a
    field its `.rq` twin doesn't (the drift above); rows only `sparql` has
    would mean the reverse."""
    paths = PARITY_FIXTURES[name]
    both, sparql_only = _rows(paths, "both"), _rows(paths, "sparql")

    shacl_extra = sorted(both - sparql_only)
    sparql_extra = sorted(sparql_only - both)
    assert shacl_extra == [], (
        f"{name}: --engine both reports rows --engine sparql does not: {shacl_extra} -- "
        "a SHACL shape and its .rq twin describe the same finding differently, so the two "
        "no longer dedup into one row"
    )
    assert sparql_extra == [], (
        f"{name}: --engine sparql reports rows --engine both does not: {sparql_extra}"
    )
