"""Covers dataquality.data_quality.check_conformance's domain/range
satisfaction logic directly -- in particular owl:Thing/rdfs:Resource/
rdfs:Literal as universal classes, found as a real false-positive against
real FOAF data (foaf:name declares both rdfs:domain owl:Thing and
rdfs:range rdfs:Literal, FOAF's own "usable on absolutely anything"
convention) while building an external SemOps worked example against this
suite. Confirmed against the real foaf.rdf fetched for that example, not
just a hand-written approximation.
"""
import rdflib

from ontology_suite.dataquality import data_quality as dq

FOAF_NAME_ONTOLOGY = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
foaf:Person a owl:Class .
foaf:name a owl:DatatypeProperty ; rdfs:domain owl:Thing ; rdfs:range rdfs:Literal .
foaf:mbox a owl:ObjectProperty ; rdfs:domain owl:Thing ; rdfs:range rdfs:Resource .
"""


def _declarations(ttl):
    return dq.ontology_declarations(rdflib.Graph().parse(data=ttl, format="turtle"))


def test_owl_thing_domain_property_has_no_false_positive_for_any_typed_subject():
    """foaf:name's rdfs:domain is owl:Thing -- every typed subject trivially
    satisfies it; the ordinary rdfs:subClassOf ancestor walk can never reach
    owl:Thing (no real ontology asserts "X rdfs:subClassOf owl:Thing" as an
    ordinary triple, since it's true axiomatically under OWL semantics)."""
    decl = _declarations(FOAF_NAME_ONTOLOGY)
    data = rdflib.Graph().parse(data="""
        @prefix foaf: <http://xmlns.com/foaf/0.1/> .
        @prefix ex: <https://example.org/> .
        ex:alice a foaf:Person ; foaf:name "Alice" .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["domain_violations"] == {}


def test_rdfs_literal_range_accepts_any_datatype():
    """foaf:name's rdfs:range is rdfs:Literal (not a specific xsd: datatype)
    -- the universal literal class, so any literal (untyped/xsd:string,
    xsd:integer, a language-tagged string, ...) satisfies it."""
    decl = _declarations(FOAF_NAME_ONTOLOGY)
    data = rdflib.Graph().parse(data="""
        @prefix foaf: <http://xmlns.com/foaf/0.1/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix ex: <https://example.org/> .
        ex:alice foaf:name "Alice" .
        ex:bob foaf:name "Bob"@en .
        ex:carol foaf:name "42"^^xsd:integer .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["range_violations"] == {}


def test_rdfs_resource_range_accepts_any_object():
    """foaf:mbox's rdfs:range is rdfs:Resource -- the universal resource
    class, same reasoning as owl:Thing above but on the range side."""
    decl = _declarations(FOAF_NAME_ONTOLOGY)
    data = rdflib.Graph().parse(data="""
        @prefix foaf: <http://xmlns.com/foaf/0.1/> .
        @prefix ex: <https://example.org/> .
        ex:alice foaf:mbox <mailto:alice@example.org> .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["range_violations"] == {}


def test_real_foaf_name_and_mbox_produce_no_false_conformance_violations():
    """The actual reported scenario, against the real fetched foaf.rdf --
    not just a hand-written approximation of what FOAF declares."""
    import os
    foaf_path = "C:/repos/testPyPI_shacl/semops_manual/reference_vocab/foaf.rdf"
    if not os.path.isfile(foaf_path):
        import pytest
        pytest.skip("real foaf.rdf fixture not present in this environment")
    ont = rdflib.Graph().parse(foaf_path)
    decl = dq.ontology_declarations(ont)
    data = rdflib.Graph().parse(data="""
        @prefix foaf: <http://xmlns.com/foaf/0.1/> .
        @prefix ex: <https://example.org/> .
        ex:alice a foaf:Person ; foaf:name "Alice" ; foaf:mbox <mailto:alice@example.org> .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["domain_violations"] == {}
    assert conf["range_violations"] == {}


def test_genuine_domain_violation_still_caught():
    """A property whose domain is a specific class (not owl:Thing) still
    correctly flags a subject of the wrong type -- the fix must not
    over-broaden satisfaction beyond the two universal classes."""
    decl = _declarations("""
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix ex: <https://example.org/> .
        ex:Widget a owl:Class .
        ex:Gadget a owl:Class .
        ex:price a owl:DatatypeProperty ; rdfs:domain ex:Widget .
    """)
    data = rdflib.Graph().parse(data="""
        @prefix ex: <https://example.org/> .
        ex:g1 a ex:Gadget ; ex:price "9.99" .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["domain_violations"] == {rdflib.URIRef("https://example.org/price"): {rdflib.URIRef("https://example.org/g1")}}


def test_genuine_range_datatype_violation_still_caught():
    """A property ranged on a specific xsd: datatype (not rdfs:Literal)
    still correctly flags a value of the wrong datatype."""
    decl = _declarations("""
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix ex: <https://example.org/> .
        ex:Widget a owl:Class .
        ex:price a owl:DatatypeProperty ; rdfs:domain ex:Widget ; rdfs:range xsd:integer .
    """)
    data = rdflib.Graph().parse(data="""
        @prefix ex: <https://example.org/> .
        ex:w1 a ex:Widget ; ex:price "not-a-number" .
    """, format="turtle")
    conf = dq.check_conformance(decl, data)
    assert conf["range_violations"] == {rdflib.URIRef("https://example.org/price"): {rdflib.Literal("not-a-number")}}
