"""Tests for checks.shacl_native_runner: the native (Rust) SHACL engine
adapter, cross-checked against pyshacl for correctness. Skipped entirely if
the optional `shacl` package isn't installed (see that module's docstring
for how to get it -- not on PyPI yet).
"""
import rdflib
import pytest

from ontology_suite import config, pipeline
from ontology_suite.checks import shacl_native_runner as native_runner
from ontology_suite.checks.merge import build_unified_results
from ontology_suite.checks.registry import Registry
from ontology_suite.checks.shacl_runner import load_shapes_graph, run_shacl

pytestmark = pytest.mark.skipif(
    not native_runner.available(), reason="native `shacl` package not installed (see shacl_native_runner.py docstring)"
)


@pytest.fixture(scope="module")
def shapes() -> rdflib.Graph:
    return load_shapes_graph(config.DEFAULT_SHAPES_DIR)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


@pytest.fixture
def domain_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse("examples/ontology/domain.ttl", format="turtle")
    return g


def _rows_for(data_graph, shapes, registry, run_fn):
    conforms, results_graph, _text = run_fn(data_graph, shapes)
    return conforms, build_unified_results(results_graph, rdflib.Graph(), registry, shapes)


def _row_key(row):
    return (row.check_id, row.focus_node, row.value)


def test_native_matches_pyshacl_on_the_deliberately_flawed_domain_fixture(domain_graph, shapes, registry):
    """examples/ontology/domain.ttl is hand-authored to trip a specific,
    known set of checks (see the file's own comments) -- the real
    cross-engine correctness proof: same finding count, same check ids,
    same focus nodes/values, zero unresolved check ids on either side."""
    conforms_py, rows_py = _rows_for(domain_graph, shapes, registry, run_shacl)
    conforms_native, rows_native = _rows_for(domain_graph, shapes, registry, native_runner.run_shacl_native)

    assert conforms_py == conforms_native
    assert {_row_key(r) for r in rows_py} == {_row_key(r) for r in rows_native}
    assert not any(r.check_id is None for r in rows_py)
    assert not any(r.check_id is None for r in rows_native)


def test_native_resolves_check_id_for_a_blank_node_rooted_shape(domain_graph, shapes, registry):
    """LOG-001's constraint lives in an anonymous sh:property [...] shape
    (native SHACL core, not SPARQL) -- the specific case that needs the
    sh:message-based fallback correlation described in this module's
    docstring, since the native engine's own blank node identity can't be
    matched against `shapes` directly."""
    _conforms, rows = _rows_for(domain_graph, shapes, registry, native_runner.run_shacl_native)
    assert any(r.check_id == "LOG-001" for r in rows)


def test_native_resolves_check_id_for_a_sparql_based_shape(domain_graph, shapes, registry):
    """EFF-001 is SHACL-SPARQL (sh:sparql [...]); both engines report the
    *enclosing named shape* directly for this kind (no blank-node fallback
    needed) -- covered separately from the blank-node case above so a
    regression in either path is caught precisely."""
    _conforms, rows = _rows_for(domain_graph, shapes, registry, native_runner.run_shacl_native)
    assert any(r.check_id == "EFF-001" for r in rows)


def test_run_shacl_native_matches_run_shacl_return_shape(domain_graph, shapes):
    """Drop-in signature check: (conforms, results_graph, results_text)."""
    conforms, results_graph, text = native_runner.run_shacl_native(domain_graph, shapes)
    assert isinstance(conforms, bool)
    assert isinstance(results_graph, rdflib.Graph)
    assert isinstance(text, str)
    assert conforms is False
    assert len(results_graph) > 0


def test_run_shacl_native_accepts_rdfs_inference(domain_graph, shapes):
    """shacl>=0.1.3 materialises RDFS entailments into the data graph before
    validating when asked -- just needs to run without error and return the
    same shape as the default; the entailment semantics themselves are the
    native engine's own responsibility, not this adapter's."""
    conforms, results_graph, _text = native_runner.run_shacl_native(domain_graph, shapes, inference="rdfs")
    assert isinstance(conforms, bool)
    assert isinstance(results_graph, rdflib.Graph)


def test_run_shacl_native_rejects_owlrl_inference(domain_graph, shapes):
    """The native engine has no OWL2-RL reasoner -- confirms it still raises
    for a value this adapter doesn't itself validate (pipeline.py rejects it
    earlier, but the adapter shouldn't silently accept it if called directly)."""
    with pytest.raises(ValueError):
        native_runner.run_shacl_native(domain_graph, shapes, inference="owlrl")


def test_default_engine_prefers_native_when_available():
    """This module only runs when native_runner.available() is True (see
    pytestmark above), so default_engine() must reflect that."""
    assert pipeline.default_engine() == "native+sparql"


def test_pipeline_rejects_owlrl_inference_under_native_engine(domain_graph, registry):
    with pytest.raises(ValueError):
        pipeline.run_registry_suite_on_graph(
            domain_graph, registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR,
            inference="owlrl", engine="native",
        )


def test_pipeline_rejects_both_inference_under_native_plus_sparql_engine(domain_graph, registry):
    with pytest.raises(ValueError):
        pipeline.run_registry_suite_on_graph(
            domain_graph, registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR,
            inference="both", engine="native+sparql",
        )


def test_pipeline_accepts_rdfs_inference_under_native_engine(domain_graph, registry):
    rows = pipeline.run_registry_suite_on_graph(
        domain_graph, registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR,
        inference="rdfs", engine="native",
    )
    assert isinstance(rows, list)


def test_clean_data_runs_without_error(shapes):
    data_graph = rdflib.Graph()
    data_graph.parse(
        data="@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        '<https://example.org/demo/Thing> a owl:Class ; rdfs:label "Thing"@en .\n',
        format="turtle",
    )
    conforms, results_graph, _text = native_runner.run_shacl_native(data_graph, shapes)
    # not necessarily fully clean (style/quality checks may still fire), but must run without error
    assert isinstance(conforms, bool)
    assert isinstance(results_graph, rdflib.Graph)
