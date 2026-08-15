"""`checks/merge.py` must produce the same rows for the same input, run to
run.

Two things in the SHACL result vocabulary are *sets*, not single values,
and reading either of them with `Graph.value()` picks an arbitrary member:

* `sh:value` -- several of this suite's CONSTRUCTs deliberately bind two
  per finding (`LOG-004`'s two inverses, `LOG-006`/`LOG-007`'s domain and
  range, `REA-001`'s two disjoint classes, `STR-007`'s subject and object);
* `sh:resultPath` -- `LOG-006`/`LOG-007` bind both `rdfs:domain` and
  `rdfs:range`, and a SHACL *path expression* (`LOG-001`'s
  `[ sh:oneOrMorePath rdfs:subClassOf ]`) is a blank node whose identifier
  rdflib mints fresh on every parse.

Both feed the `(check_id, focus_node, path, value)` dedup key, so both used
to make finding *counts* fluctuate with no input change: running one
unchanged fixture three times in a row reported `LOG-004` three, then two,
then four times. That makes count-based CI gates and `full_results.csv`
diffs unreliable, and it is invisible in a single run -- hence these tests.
"""
import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, Namespace

from ontology_suite import config
from ontology_suite.checks.merge import build_unified_results
from ontology_suite.checks.registry import Registry

SH = Namespace("http://www.w3.org/ns/shacl#")
OQ = Namespace("https://semantechs.co.uk/ontology-quality/")
EX = Namespace("https://example.org/demo/")


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


def _result(graph: Graph, check_id: str, focus, *, values=(), paths=()) -> BNode:
    node = BNode()
    graph.add((node, RDF.type, SH.ValidationResult))
    graph.add((node, SH.resultSeverity, SH.Violation))
    graph.add((node, SH.focusNode, focus))
    graph.add((node, SH.sourceConstraintComponent, OQ[check_id]))
    graph.add((node, SH.resultMessage, Literal(f"{check_id} on {focus}")))
    for value in values:
        graph.add((node, SH.value, value))
    for path in paths:
        graph.add((node, SH.resultPath, path))
    return node


def test_two_values_in_either_order_are_one_finding(registry):
    """`LOG-004`'s WHERE clause is symmetric in `?p1`/`?p2` -- it matches
    both (a, b) and (b, a) and emits a result for each, describing the one
    same contradiction. They must collapse to a single row, and the row must
    name both properties rather than an arbitrary one of them."""
    results = Graph()
    _result(results, "LOG-004", EX.hasParent, values=[EX.hasChild, EX.childOf], paths=[EX.inverseOf])
    _result(results, "LOG-004", EX.hasParent, values=[EX.childOf, EX.hasChild], paths=[EX.inverseOf])

    rows = build_unified_results(results, Graph(), registry)

    assert len(rows) == 1
    assert rows[0].value == f"{EX.childOf}, {EX.hasChild}"


def test_multiple_result_paths_render_in_a_stable_order(registry):
    """`LOG-006`/`LOG-007` bind `sh:resultPath rdfs:domain, rdfs:range` on
    one result. Reading one of the two arbitrarily is what made the same
    finding land under a different dedup key between runs."""
    results = Graph()
    _result(results, "LOG-006", EX.knows, values=[EX.Person, EX.Place], paths=[RDFS.domain, RDFS.range])

    rows = build_unified_results(results, Graph(), registry)

    assert len(rows) == 1
    assert rows[0].path == f"{RDFS.domain}, {RDFS.range}"


def test_path_expression_renders_as_a_property_path_not_a_blank_node_id(registry):
    """`LOG-001`'s `sh:path [ sh:oneOrMorePath rdfs:subClassOf ]` reaches
    `merge.py` as a blank node. `str()` on it yields a per-parse identifier
    (`n40a4cfc3ac...`) -- unstable across runs, meaningless in a report, and
    never equal between the pyshacl and SPARQL formulations of the same
    finding, so they could not dedup."""
    results = Graph()
    path = BNode()
    results.add((path, SH.oneOrMorePath, RDFS.subClassOf))
    _result(results, "LOG-001", EX.Reptile, values=[EX.Animal], paths=[path])

    rows = build_unified_results(results, Graph(), registry)

    assert rows[0].path == f"({RDFS.subClassOf})+"


@pytest.mark.parametrize(
    "build, expected",
    [
        pytest.param(
            lambda g, n: [g.add((n, SH.inversePath, RDFS.subClassOf))],
            f"^({RDFS.subClassOf})",
            id="inverse",
        ),
        pytest.param(
            lambda g, n: [g.add((n, SH.zeroOrMorePath, RDFS.subClassOf))],
            f"({RDFS.subClassOf})*",
            id="zero-or-more",
        ),
        pytest.param(
            lambda g, n: [g.add((n, SH.zeroOrOnePath, RDFS.subClassOf))],
            f"({RDFS.subClassOf})?",
            id="zero-or-one",
        ),
        pytest.param(
            lambda g, n: [
                Collection(g, n, [RDFS.subClassOf, RDF.type]),
            ],
            f"{RDFS.subClassOf}/{RDF.type}",
            id="sequence",
        ),
    ],
)
def test_path_expression_covers_the_shacl_path_vocabulary(registry, build, expected):
    """Only `sh:oneOrMorePath` is used by this suite's own shapes today, but
    a check added later can use any of these, and an unrendered one silently
    reintroduces the blank-node-id problem for that check alone."""
    results = Graph()
    path = BNode()
    build(results, path)
    _result(results, "LOG-001", EX.Reptile, values=[EX.Animal], paths=[path])

    rows = build_unified_results(results, Graph(), registry)

    assert rows[0].path == expected


def test_unrecognized_path_structure_degrades_instead_of_raising(registry):
    """A blank node that is not any known path expression should still
    produce a row -- degraded output beats an exception that loses every
    other finding in the run alongside it."""
    results = Graph()
    path = BNode()
    results.add((path, URIRef("https://example.org/notAPath"), RDFS.subClassOf))
    _result(results, "LOG-001", EX.Reptile, values=[EX.Animal], paths=[path])

    rows = build_unified_results(results, Graph(), registry)

    assert rows[0].path == str(path)


def test_repeated_runs_over_the_same_fixture_agree(registry):
    """The end-to-end version of the above: the same graph through the full
    registry suite, three times, must give byte-identical rows. This is the
    test that would have failed on the LOG-004 3/2/4 observation."""
    from ontology_suite import pipeline

    graph = Graph().parse("examples/ontology/domain.ttl", format="turtle")

    def signature():
        rows = pipeline.run_registry_suite_on_graph(graph, registry, engine="both")
        # `path`/`value` are legitimately None for checks that bind neither.
        return sorted(
            (r.check_id, r.severity, r.focus_node, r.path or "", r.value or "") for r in rows
        )

    first = signature()
    assert first == signature() == signature()
