"""Unit tests for ontology_suite.versioning.diff's semver-style bump
classifier -- one engineered old/new pair per rule in the module docstring.
"""
from rdflib import Graph

from ontology_suite.versioning.diff import BumpLevel, diff_ontologies

PREFIXES = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <https://example.org/demo/> .
"""


def g(ttl: str) -> Graph:
    graph = Graph()
    graph.parse(data=PREFIXES + ttl, format="turtle")
    return graph


BASE = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Dog ; rdfs:range ex:Animal .
"""


def bump_between(old_extra: str, new_extra: str) -> BumpLevel:
    _diff, bump = diff_ontologies(g(BASE + old_extra), g(BASE + new_extra))
    return bump


def test_identical_ontologies_are_none():
    assert bump_between("", "") == BumpLevel.NONE


def test_added_class_is_minor():
    assert bump_between("", "ex:Cat a owl:Class .") == BumpLevel.MINOR


def test_removed_class_is_major():
    diff, bump = diff_ontologies(g(BASE), g("ex:Animal a owl:Class ."))
    assert bump == BumpLevel.MAJOR
    assert diff.removed_classes == {__import__("rdflib").URIRef("https://example.org/demo/Dog")}


def test_removed_subclass_edge_is_major():
    old = BASE
    new = """
ex:Animal a owl:Class .
ex:Dog a owl:Class .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Dog ; rdfs:range ex:Animal .
"""
    diff, bump = diff_ontologies(g(old), g(new))
    assert bump == BumpLevel.MAJOR
    assert diff.removed_subclass_edges


def test_added_disjointness_is_major():
    assert bump_between("", "ex:Dog owl:disjointWith ex:Animal .") == BumpLevel.MAJOR


def test_removed_disjointness_is_minor():
    assert bump_between("ex:Dog owl:disjointWith ex:Cat .\nex:Cat a owl:Class .", "ex:Cat a owl:Class .") == BumpLevel.MINOR


def test_narrowed_domain_is_major():
    old = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:Cat a owl:Class ; rdfs:subClassOf ex:Animal .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Animal ; rdfs:range ex:Animal .
"""
    new = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:Cat a owl:Class ; rdfs:subClassOf ex:Animal .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Dog ; rdfs:range ex:Animal .
"""
    diff, bump = diff_ontologies(g(old), g(new))
    assert bump == BumpLevel.MAJOR
    assert "https://example.org/demo/hasOwner" in {str(k) for k in diff.narrowed_domain}


def test_widened_domain_is_minor():
    old = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Dog ; rdfs:range ex:Animal .
"""
    new = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:hasOwner a owl:ObjectProperty ; rdfs:domain ex:Animal ; rdfs:range ex:Animal .
"""
    diff, bump = diff_ontologies(g(old), g(new))
    assert bump == BumpLevel.MINOR
    assert diff.widened_domain


def test_domain_added_from_unconstrained_is_major():
    """A property with no prior domain gaining one is a narrowing (breaking),
    not a widening, even though the empty set is a subset of everything."""
    old = "ex:hasNickname a owl:DatatypeProperty ."
    new = "ex:hasNickname a owl:DatatypeProperty ; rdfs:domain ex:Animal ."
    diff, bump = diff_ontologies(g(BASE + old), g(BASE + new))
    assert bump == BumpLevel.MAJOR
    assert "https://example.org/demo/hasNickname" in {str(k) for k in diff.narrowed_domain}


def test_new_property_domain_is_not_flagged_as_narrowing():
    """A brand-new property's domain shouldn't itself trigger MAJOR -- only
    the (already-MINOR) fact that the property is new."""
    new_extra = "ex:hasNickname a owl:DatatypeProperty ; rdfs:domain ex:Dog ."
    diff, bump = diff_ontologies(g(BASE), g(BASE + new_extra))
    assert bump == BumpLevel.MINOR
    assert not diff.narrowed_domain


def test_added_restricting_characteristic_is_major():
    assert bump_between("", "ex:hasOwner a owl:FunctionalProperty .") == BumpLevel.MAJOR


def test_removed_restricting_characteristic_is_minor():
    old_extra = "ex:hasOwner a owl:FunctionalProperty ."
    diff, bump = diff_ontologies(g(BASE + old_extra), g(BASE))
    assert bump == BumpLevel.MINOR
    assert diff.removed_characteristics


def test_removed_equivalent_class_is_major():
    old_extra = "ex:Dog owl:equivalentClass ex:Canine .\nex:Canine a owl:Class ."
    diff, bump = diff_ontologies(g(BASE + old_extra), g(BASE + "ex:Canine a owl:Class ."))
    assert bump == BumpLevel.MAJOR
    assert diff.removed_equivalent_classes


def test_added_equivalent_class_is_minor():
    new_extra = "ex:Dog owl:equivalentClass ex:Canine .\nex:Canine a owl:Class ."
    assert bump_between("ex:Canine a owl:Class .", new_extra) == BumpLevel.MINOR


def test_label_only_change_is_patch():
    old_extra = 'ex:Dog rdfs:label "Dog"@en .'
    new_extra = 'ex:Dog rdfs:label "Doggo"@en .'
    assert bump_between(old_extra, new_extra) == BumpLevel.PATCH
