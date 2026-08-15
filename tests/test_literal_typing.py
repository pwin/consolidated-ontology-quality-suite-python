"""`DAT-001` must catch an invalid literal that rdflib rewrote on parse.

The portable formulations (`sparql/data/DAT-001.rq` and its `shapes/data.ttl`
twin) regex the *stored lexical form*. For `xsd:date` and `xsd:integer` that
is the authored text, so they work. For `xsd:boolean` it is not: rdflib's
boolean converter never raises -- it warns and yields `False` -- and with
`NORMALIZE_LITERALS` on (the default) the literal is re-serialized from that
value, so `"yes"^^xsd:boolean` reaches every check as `'false'`, which
matches the regex. The boolean branch was unreachable under rdflib.

`checks/literal_typing.py` closes that by asking rdflib what it actually
concluded (`Literal.ill_typed`) instead of re-deriving it from text, which
also picks up value-space violations no lexical pattern can express.
"""
import pytest
from rdflib import Graph, Namespace

from ontology_suite import config, pipeline
from ontology_suite.checks.literal_typing import SOURCE_LABEL, run_literal_typing_check
from ontology_suite.checks.registry import Registry

EX = Namespace("https://example.org/demo/")
SH = Namespace("http://www.w3.org/ns/shacl#")

TURTLE_HEADER = """
@prefix ex:  <https://example.org/demo/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""


def _graph(body: str) -> Graph:
    return Graph().parse(data=TURTLE_HEADER + body, format="turtle")


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


def _dat001(graph, registry, engine="both"):
    rows = pipeline.run_registry_suite_on_graph(graph, registry, engine=engine)
    return [r for r in rows if r.check_id == "DAT-001"]


def test_invalid_boolean_is_reported(registry):
    """The finding the regex formulation structurally cannot make."""
    rows = _dat001(_graph('ex:a ex:isActive "yes"^^xsd:boolean .'), registry)

    assert len(rows) == 1
    assert rows[0].severity == "Violation"
    assert rows[0].sources == [SOURCE_LABEL]


def test_boolean_message_does_not_quote_rdflibs_rewrite_as_authored_text(registry):
    """`"yes"` is unrecoverable from the parsed graph -- the stored form is
    already `'false'`. Reporting "Literal 'false' is invalid" would be
    actively confusing, since `'false'` is a perfectly good boolean."""
    rows = _dat001(_graph('ex:a ex:isActive "yes"^^xsd:boolean .'), registry)

    assert "no longer present in the parsed graph" in rows[0].message


@pytest.mark.parametrize(
    "literal",
    ['"twelve"^^xsd:integer', '"31-12-1990"^^xsd:date'],
    ids=["integer", "date"],
)
def test_lexically_invalid_literals_stay_a_single_finding(registry, literal):
    """These are found by all three formulations at once. They must merge
    into one row under `merge.py`'s dedup key, not triple-report -- the
    supplement adds coverage, it does not add duplicates."""
    rows = _dat001(_graph(f"ex:a ex:prop {literal} ."), registry)

    assert len(rows) == 1
    assert rows[0].sources == [SOURCE_LABEL, "shacl", "sparql"]


def test_lexically_valid_but_impossible_date_is_reported(registry):
    """`2021-02-30` matches `^-?[0-9]{4}-[0-9]{2}-[0-9]{2}$` and is not a
    date. A lexical regex cannot express "and February has 28 days"; the
    parser already knows."""
    rows = _dat001(_graph('ex:a ex:when "2021-02-30"^^xsd:date .'), registry)

    assert len(rows) == 1
    assert rows[0].sources == [SOURCE_LABEL]


def test_valid_literals_are_not_reported(registry):
    """False-positive guard, including the two forms most likely to be
    mistaken for ill-typed: a plain (untyped) string, and a datatype rdflib
    has no converter for, where `ill_typed` is None rather than False."""
    graph = _graph(
        """
        ex:a ex:isActive "true"^^xsd:boolean ;
             ex:count    "12"^^xsd:integer ;
             ex:when     "1990-12-31"^^xsd:date ;
             ex:note     "just a string" ;
             ex:custom   "whatever"^^ex:MadeUpDatatype .
        """
    )
    assert _dat001(graph, registry) == []


def test_every_engine_reports_the_same_dat001_findings(registry):
    """The supplement is about a blind spot in the *check formulations*, not
    in one engine, so `--engine` must not change which literals are flagged.
    Anything else reintroduces the engine-dependent finding set."""
    graph = _graph(
        """
        ex:a ex:isActive "yes"^^xsd:boolean ;
             ex:count    "twelve"^^xsd:integer .
        """
    )
    by_engine = {
        engine: sorted((r.focus_node, r.path, r.value) for r in _dat001(graph, registry, engine))
        for engine in ("both", "sparql", "shacl")
    }
    assert by_engine["both"] == by_engine["sparql"] == by_engine["shacl"]
    assert len(by_engine["both"]) == 2


def test_check_runs_standalone_over_a_graph():
    """Direct unit of the results graph, independent of the pipeline: one
    `sh:ValidationResult` per ill-typed literal, and nothing for the rest."""
    graph = _graph(
        """
        ex:a ex:isActive "yes"^^xsd:boolean ;
             ex:ok       "true"^^xsd:boolean .
        """
    )
    results = run_literal_typing_check(graph)

    reported = list(results.subject_objects(SH.resultPath))
    assert [str(path) for _result, path in reported] == [str(EX.isActive)]
    assert (reported[0][0], SH.resultSeverity, SH.Violation) in results
