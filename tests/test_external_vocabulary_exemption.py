"""Checks that ask "is this term declared here?" must agree with each other
about which terms are somebody else's to declare.

Eight of them exempt `http://www.w3.org/` wholesale, on the reasoning that
external W3C vocabulary is defined by its own spec, not by the graph under
test. `STR-002` used to be the odd one out: it exempted `rdf:`, `rdfs:` and
`owl:` by their three individual namespace IRIs only. So an ontology that
used `skos:prefLabel` -- the label predicate `QUA-001`/`QUA-002`/`QUA-004`
all accept as valid, and that `dataquality/data_quality.py`'s own
`ANNOTATION_PREDICATES` set recognizes by name -- got a Violation-severity
"undefined property used" from `STR-002` while its own sibling `STR-007`
("predicate has no declared rdf:type", the strictly broader question)
stayed quiet about the same predicate on the same graph. Two checks
disagreeing with each other about one term is a bug whichever way it is
resolved; this pins the resolution.

`STR-001` is the deliberate exception and is asserted as such below: it
exempts an explicit list of built-in *classes* legitimately used as an
`rdf:type` value, which is a narrower question than "whose job is it to
declare this predicate".
"""
import pytest
from rdflib import Graph

from ontology_suite import config, pipeline
from ontology_suite.checks.registry import Registry

# Uses skos:prefLabel and dcterms:title without redeclaring either locally.
# SKOS is W3C-published; Dublin Core is not, which is what makes this a
# discriminating fixture rather than a blanket "external terms are fine".
ONTOLOGY = """
@prefix owl:     <http://www.w3.org/2002/07/owl#> .
@prefix rdfs:    <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos:    <http://www.w3.org/2004/02/skos/core#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix ex:      <https://example.org/demo/> .

<https://example.org/demo> a owl:Ontology ;
  owl:versionInfo "1.0.0" ;
  dcterms:title "Demo" ;
  rdfs:label "Demo"@en .

ex:Widget a owl:Class ;
  rdfs:label "Widget"@en ;
  skos:prefLabel "Widget"@en .
"""

# Every check that exempts `http://www.w3.org/`, mapped to the ResultRow
# field naming the term it is complaining about. It is not always the focus
# node: STR-002 reports the *predicate* (as `sh:resultPath`) on the subject
# that used it, and STR-006/DAT-002 report the *object* (as `sh:value`).
# Reading the wrong field would make this test either vacuous or falsely
# red -- QUA-004 and STY-004 both emit a constant `sh:resultPath
# skos:prefLabel`, which is W3C-namespaced by construction and says nothing
# about what they flagged.
TERM_FIELD = {
    "STR-002": "path",
    "STR-004": "focus_node",
    "STR-006": "value",
    "STR-007": "focus_node",
    "STR-009": "focus_node",
    "DAT-002": "value",
    "QUA-004": "focus_node",
    "STY-004": "focus_node",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


@pytest.fixture(scope="module")
def rows(registry):
    graph = Graph().parse(data=ONTOLOGY, format="turtle")
    return pipeline.run_registry_suite_on_graph(graph, registry, engine="both")


def test_w3c_vocabulary_is_exempt_from_every_check_that_claims_to_exempt_it(rows):
    """`skos:prefLabel` is used and not declared locally. No check that
    exempts `http://www.w3.org/` may flag it -- and after the fix, STR-002
    is one of them."""
    flagged = sorted(
        {(r.check_id, term)
         for r in rows
         if r.check_id in TERM_FIELD
         and (term := getattr(r, TERM_FIELD[r.check_id]) or "").startswith("http://www.w3.org/")}
    )
    assert flagged == [], f"W3C vocabulary flagged by checks that exempt it: {flagged}"


def test_every_exempting_check_declares_the_same_exemption(rows):
    """The source-level counterpart: the exemption must actually be written
    in each `.rq` file, so a check that simply never fires on this fixture
    can't pass the behavioral test above by accident."""
    missing = [
        check_id
        for check_id in TERM_FIELD
        for path in config.DEFAULT_SPARQL_DIR.rglob(f"{check_id}.rq")
        if 'STRSTARTS(STR(?' not in path.read_text(encoding="utf-8")
        or 'http://www.w3.org/"))' not in path.read_text(encoding="utf-8")
    ]
    assert missing == [], f"{missing} no longer exempt the http://www.w3.org/ namespace"


def test_str002_and_str007_agree_about_the_same_predicate(rows):
    """The narrower check (STR-002: "not declared as one of the four
    property types") must not fire on a predicate the broader one
    (STR-007: "has no rdf:type at all") lets through. Fire-together or
    stay-silent-together; anything else is the two disagreeing."""
    str002 = {r.path for r in rows if r.check_id == "STR-002"}
    str007 = {r.focus_node for r in rows if r.check_id == "STR-007"}
    assert str002 <= str007, (
        f"STR-002 flags {sorted(str002 - str007)}, which STR-007 exempts -- "
        "the two checks' namespace exemptions have drifted apart again"
    )


def test_non_w3c_external_vocabulary_is_still_flagged(rows):
    """The exemption is specifically for W3C-published vocabulary, not for
    "anything external" -- `dcterms:title` is used here and undeclared, and
    should still be reported. Without this, broadening STR-002 to a whole
    namespace prefix could be quietly widened further to no check at all."""
    undeclared = {r.path for r in rows if r.check_id == "STR-002"}
    assert "http://purl.org/dc/terms/title" in undeclared


def test_str001_keeps_its_own_term_list_rather_than_the_namespace_prefix():
    """STR-001's exemption is deliberately a different shape from its
    siblings' -- asserted so a future "make them all consistent" pass
    doesn't sweep it up. A namespace-wide prefix there would exempt every
    W3C class from ever being reported as undefined, which is a much
    broader claim than "these specific built-ins are axiomatic"."""
    query = (config.DEFAULT_SPARQL_DIR / "structural" / "STR-001.rq").read_text(encoding="utf-8")
    assert 'STRSTARTS(STR(?class), "http://www.w3.org/")' not in query
    assert "?class NOT IN (" in query
