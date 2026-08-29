"""QUA-009 counts skos:prefLabel *per language*, not outright.

The check was first written as "exactly one skos:prefLabel, full stop". That
is wrong about SKOS, which defines prefLabel as unique per language tag -- a
bilingual ontology carrying "Road"@en and "Ffordd"@cy is correct, and a check
flagging it would be enforcing a rule SKOS does not have.

The subtlety that makes this worth its own module is the **untagged** case.
SHACL's `sh:uniqueLang` compares language tags, and two untagged literals do
not share a tag, so they pass it -- confirmed here against both engines. But
untagged is precisely how gist-based ontologies label everything
(`"Event"^^xsd:string`), so relying on `sh:uniqueLang` alone would let the
commonest form of the defect through in exactly the ontologies this suite is
aimed at. The shape pairs it with a qualified cardinality on `xsd:string`,
and the `.rq` twin gets the same effect from `LANG()` returning `""`.

Every assertion below runs across all three engines, because this is a case
where they could easily disagree: the constraint is expressed in SHACL core
on one side and as an aggregate query on the other.
"""
import pytest
import rdflib

from ontology_suite import config, pipeline
from ontology_suite.checks.registry import Registry
from ontology_suite.checks.shacl_native_runner import available as native_available

FIXTURE = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex:   <https://example.org/langs/> .

<https://example.org/langs> a owl:Ontology ;
  owl:versionIRI <https://example.org/langs/1.0.0> ;
  rdfs:label "language fixture"@en ;
  skos:prefLabel "language fixture"@en .

# --- must NOT be reported -------------------------------------------------
ex:OneTagged   a owl:Class ; skos:prefLabel "One"@en .
ex:OneUntagged a owl:Class ; skos:prefLabel "One" .
ex:Bilingual   a owl:Class ; skos:prefLabel "Road"@en , "Ffordd"@cy .
ex:Trilingual  a owl:Class ; skos:prefLabel "Road"@en , "Ffordd"@cy , "Rathad"@gd .
ex:TaggedPlusPlain a owl:Class ; skos:prefLabel "Road"@en , "Road" .

# --- must be reported -----------------------------------------------------
ex:NoLabel       a owl:Class .
ex:TwoSameTag    a owl:Class ; skos:prefLabel "Road"@en , "Roadway"@en .
ex:TwoUntagged   a owl:Class ; skos:prefLabel "Road" , "Roadway" .
ex:ThreeUntagged a owl:Class ; skos:prefLabel "A" , "B" , "C" .
"""

SHOULD_FIRE = {"NoLabel", "TwoSameTag", "TwoUntagged", "ThreeUntagged"}
SHOULD_PASS = {"OneTagged", "OneUntagged", "Bilingual", "Trilingual", "TaggedPlusPlain"}

ENGINES = ["sparql", "shacl"] + (["native"] if native_available() else [])


@pytest.fixture(scope="module")
def graph():
    g = rdflib.Graph()
    g.parse(data=FIXTURE, format="turtle")
    return g


@pytest.fixture(scope="module")
def registry():
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


def _fired(graph, registry, engine):
    rows = pipeline.run_registry_suite_on_graph(graph, registry, engine=engine)
    return {r.focus_node.rsplit("/", 1)[-1] for r in rows if r.check_id == "QUA-009"}


@pytest.mark.parametrize("engine", ENGINES)
def test_exact_firing_set(graph, registry, engine):
    assert _fired(graph, registry, engine) == SHOULD_FIRE


@pytest.mark.parametrize("engine", ENGINES)
def test_several_languages_are_not_a_finding(graph, registry, engine):
    """The reason this check is per-language rather than absolute. SKOS
    permits one prefLabel per language; a bilingual or trilingual term is
    correct, and so is one tagged label alongside one untagged."""
    fired = _fired(graph, registry, engine)
    assert not (fired & SHOULD_PASS), f"{engine} reported terms that are correct SKOS: {fired & SHOULD_PASS}"


@pytest.mark.parametrize("engine", ENGINES)
def test_untagged_duplicates_are_caught(graph, registry, engine):
    """sh:uniqueLang alone does not catch these -- it compares tags, and
    neither value has one. gist labels every term untagged, so this is the
    case that matters most in practice and the one a naive shape misses."""
    assert "TwoUntagged" in _fired(graph, registry, engine)
    assert "ThreeUntagged" in _fired(graph, registry, engine)


def test_all_engines_agree(graph, registry):
    """The constraint is SHACL core on one side and an aggregate query on the
    other, so agreement here is a real signal rather than a tautology."""
    results = {engine: _fired(graph, registry, engine) for engine in ENGINES}
    assert len(set(map(frozenset, results.values()))) == 1, f"engines disagree: {results}"


def test_findings_merge_into_one_row_per_term(graph, registry):
    """Both formulations must bind the same sh:resultPath and no sh:value, or
    the identical finding survives twice under --engine both."""
    rows = [r for r in pipeline.run_registry_suite_on_graph(graph, registry, engine="both")
            if r.check_id == "QUA-009"]
    assert len(rows) == len(SHOULD_FIRE)
    for row in rows:
        assert row.sources == ["shacl", "sparql"], f"{row.focus_node} did not merge: {row.sources}"
        assert row.path == "http://www.w3.org/2004/02/skos/core#prefLabel"


def test_message_distinguishes_missing_from_duplicated(graph, registry):
    rows = {r.focus_node.rsplit("/", 1)[-1]: r
            for r in pipeline.run_registry_suite_on_graph(graph, registry, engine="sparql")
            if r.check_id == "QUA-009"}
    assert "no skos:prefLabel" in rows["NoLabel"].message
    assert "untagged" in rows["TwoUntagged"].message
    assert "@en" in rows["TwoSameTag"].message
