"""Covers versioning.rename_detection: the local-name-similarity heuristic
and the explicit owl:equivalentClass/equivalentProperty/dcterms:isReplacedBy
migration-annotation signal, plus the case neither signal can catch.
"""
import rdflib

from ontology_suite.versioning.diff import diff_ontologies
from ontology_suite.versioning.rename_detection import detect_renames

EX = "https://example.org/demo/"


def _graph(turtle: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=f"@prefix ex: <{EX}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n" + turtle, format="turtle")
    return g


def test_identical_local_name_different_namespace_is_detected_by_similarity():
    """A namespace-only bump (same local name) needs no explicit annotation --
    the local-name match alone is confidence 1.0."""
    old = rdflib.Graph()
    old.parse(data="@prefix ex1: <https://example.org/v1/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . ex1:Widget a owl:Class .", format="turtle")
    new = rdflib.Graph()
    new.parse(data="@prefix ex2: <https://example.org/v2/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . ex2:Widget a owl:Class .", format="turtle")

    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert len(renames) == 1
    r = renames[0]
    assert r.old_iri == "https://example.org/v1/Widget"
    assert r.new_iri == "https://example.org/v2/Widget"
    assert r.confidence == 1.0
    assert not r.local_name_changed
    assert r.namespace_changed


def test_unrelated_local_names_are_not_matched_without_explicit_annotation():
    """'Widget' -> 'Product' shares no textual similarity -- without a
    migration annotation, this must NOT be reported as a rename (there is
    no safe way to infer it structurally)."""
    old = _graph('ex:Widget a owl:Class .\n')
    new = _graph('ex:Product a owl:Class .\n')
    diff, _bump = diff_ontologies(old, new)
    assert detect_renames(diff, new) == []


def test_explicit_equivalent_class_annotation_is_detected_with_full_confidence():
    old = _graph('ex:Widget a owl:Class .\n')
    new = _graph('ex:Product a owl:Class .\nex:Widget owl:equivalentClass ex:Product .\n')
    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert len(renames) == 1
    r = renames[0]
    assert (r.kind, r.old_iri, r.new_iri, r.confidence) == ("class", EX + "Widget", EX + "Product", 1.0)
    assert "equivalentClass" in r.reason


def test_explicit_equivalent_class_annotation_is_detected_in_reverse_direction():
    """owl:equivalentClass is logically symmetric -- asserting it from the
    *new* term ("here's what this replaces") is just as valid as from the
    old one, and just as recognizable. Found as a real gap building a
    worked example against this suite: only the old->new direction was
    checked, so the equally-valid new->old direction silently fell back to
    the lower-confidence local-name-similarity signal instead."""
    old = _graph('ex:Widget a owl:Class .\n')
    new = _graph('ex:Product a owl:Class ; owl:equivalentClass ex:Widget .\n')
    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert len(renames) == 1
    r = renames[0]
    assert (r.kind, r.old_iri, r.new_iri, r.confidence) == ("class", EX + "Widget", EX + "Product", 1.0)
    assert "equivalentClass" in r.reason


def test_dcterms_is_replaced_by_stays_one_directional():
    """dcterms:isReplacedBy is directional by definition (the subject is
    replaced by the object) -- unlike owl:equivalentClass/Property, its
    reverse direction must NOT be recognized as a migration tombstone,
    since that would nonsensically claim the new term is replaced by the
    old one. Falls back to the lower-confidence local-name-similarity
    signal instead, same as no annotation at all."""
    old = _graph('ex:Widget a owl:Class .\n')
    new = _graph(
        '@prefix dcterms: <http://purl.org/dc/terms/> .\n'
        'ex:Product a owl:Class ; dcterms:isReplacedBy ex:Widget .\n'
    )
    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert all(r.confidence < 1.0 for r in renames)


def test_explicit_equivalent_property_annotation_is_detected():
    old = _graph('ex:price a owl:DatatypeProperty .\n')
    new = _graph('ex:cost a owl:DatatypeProperty .\nex:price owl:equivalentProperty ex:cost .\n')
    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert len(renames) == 1
    assert renames[0].kind == "property"
    assert renames[0].old_iri == EX + "price"
    assert renames[0].new_iri == EX + "cost"


def test_fuzzy_local_name_match_is_lower_confidence_than_exact():
    old = _graph('ex:Widgett a owl:Class .\n')  # typo
    new = _graph('ex:Widget a owl:Class .\n')
    diff, _bump = diff_ontologies(old, new)
    renames = detect_renames(diff, new)
    assert len(renames) == 1
    assert 0.0 < renames[0].confidence < 1.0


def test_each_term_used_in_at_most_one_pairing():
    """Two removed classes, two added classes, one exact local-name match --
    greedy assignment shouldn't double-count or cross-assign."""
    old = _graph('ex:Widget a owl:Class .\nex:Gadget a owl:Class .\n')
    new = _graph('ex:Widget a owl:Class .\nex:Sprocket a owl:Class .\n')
    # ex:Widget unchanged (present in both) is not in removed/added at all;
    # ex:Gadget removed, ex:Sprocket added -- no plausible match expected.
    diff, _bump = diff_ontologies(old, new)
    assert diff.removed_classes == {rdflib.URIRef(EX + "Gadget")}
    assert diff.added_classes == {rdflib.URIRef(EX + "Sprocket")}
    renames = detect_renames(diff, new)
    assert renames == []
