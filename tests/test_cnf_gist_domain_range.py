"""CNF-003/CNF-004 read gist-style domainIncludes/rangeIncludes, not only
rdfs:domain/rdfs:range.

Before this, the two checks were not merely less sensitive on a gist-based
ontology -- they were dead. gist deliberately prefers `domainIncludes` and
`rangeIncludes`, which are `rdfs:subPropertyOf skos:scopeNote` annotations
carrying no OWL entailment, to `rdfs:domain`/`rdfs:range` for shared
properties, because a formal domain forces every use of a property into one
class and gist's whole shape is a small set of properties reused widely. So
`ontology_declarations` returned empty domain and range sets for exactly the
properties the data uses most, `check_conformance`'s `if domain_classes:`
guard skipped every one of them, and no domain or range violation could be
reported however wrong the data was.

Nothing looked broken. The report came back clean, which is the same thing a
correct ontology looks like -- the failure mode this suite has now shipped
five times and which `test_check_firing_coverage.py` exists to catch for the
registry checks. CNF-* are outside that sweep (they need two graphs), so it
did not.

The rest of the suite had already worked this out separately: STR-003 reads
them in both its formulations, `ontology_evaluation.py` folds them into its
richness metrics, and the VS Code extension's `CNF-003.rq`/`CNF-004.rq` --
which exist because that extension has no two-graph mode -- match them too.
This was the one place left that did not, and the divergence was found by
diffing the two copies of the shared registry rather than by any test.

The last test here pins the deliberate half of the asymmetry: these
annotations do *not* make a property declared. An `rdfs:domain` does, by RDFS
entailment; a scope note entails nothing about its subject, and a property
known only by one is still undeclared for CNF-002's purposes.
"""
import rdflib

from ontology_suite.dataquality import data_quality as dq

# gist has published under more than one namespace IRI over the years, which
# is why every one of these checks matches by local name. The IRI below is a
# real one; the point of the test is that it is not special.
GIST_ONTOLOGY = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix gist: <https://w3id.org/semanticarts/ns/ontology/gist/> .
@prefix ex:   <https://example.org/g/> .

ex:Road      a owl:Class .
ex:Motorway  a owl:Class ; rdfs:subClassOf ex:Road .
ex:Bridge    a owl:Class .
ex:Person    a owl:Class .

# The gist convention: soft annotations, no rdfs:domain/rdfs:range at all.
ex:hasCarriageway a owl:ObjectProperty ;
  gist:domainIncludes ex:Road ;
  gist:rangeIncludes  ex:Bridge .

# The classic convention, for contrast -- this one always worked.
ex:hasOwner a owl:ObjectProperty ;
  rdfs:domain ex:Road ;
  rdfs:range  ex:Person .
"""


def _conformance(ontology_ttl, data_ttl):
    ontology = rdflib.Graph().parse(data=ontology_ttl, format="turtle")
    data = rdflib.Graph().parse(data=data_ttl, format="turtle")
    return dq.check_conformance(dq.ontology_declarations(ontology), data)


EX = rdflib.Namespace("https://example.org/g/")


def test_domain_includes_violation_is_reported():
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:p1 a ex:Person .
        ex:b1 a ex:Bridge .
        ex:p1 ex:hasCarriageway ex:b1 .
    """)
    assert conf["domain_violations"] == {EX.hasCarriageway: {EX.p1}}


def test_range_includes_violation_is_reported():
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:r1 a ex:Road .
        ex:p1 a ex:Person .
        ex:r1 ex:hasCarriageway ex:p1 .
    """)
    assert conf["range_violations"] == {EX.hasCarriageway: {EX.p1}}


def test_conforming_gist_data_produces_nothing():
    """The half that stops this being a check that simply always fires."""
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:r1 a ex:Road .
        ex:b1 a ex:Bridge .
        ex:r1 ex:hasCarriageway ex:b1 .
    """)
    assert conf["domain_violations"] == {}
    assert conf["range_violations"] == {}


def test_subclass_of_an_included_domain_satisfies_it():
    """`domainIncludes` is folded into the same `domain` set rdfs:domain feeds,
    so it inherits the rdfs:subClassOf* walk rather than needing an exact type
    match -- which is the whole reason gist can get away with declaring the
    domain once on a shared property."""
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:m1 a ex:Motorway .
        ex:b1 a ex:Bridge .
        ex:m1 ex:hasCarriageway ex:b1 .
    """)
    assert conf["domain_violations"] == {}


def test_rdfs_domain_still_works():
    """The regression guard on the path that was never broken."""
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:p1 a ex:Person .
        ex:p2 a ex:Person .
        ex:p1 ex:hasOwner ex:p2 .
    """)
    assert conf["domain_violations"] == {EX.hasOwner: {EX.p1}}


def test_rows_carry_the_registry_ids_and_name_the_annotation():
    """A reviewer reading the finding has to be able to tell which of the two
    declarations they are being held to, since only one of them is an axiom."""
    conf = _conformance(GIST_ONTOLOGY, """
        @prefix ex: <https://example.org/g/> .
        ex:p1 a ex:Person .
        ex:b1 a ex:Bridge .
        ex:p1 ex:hasCarriageway ex:b1 .
    """)
    rows = {r.check_id: r for r in dq.conformance_to_rows(conf, "data")}
    assert "domainIncludes" in rows["CNF-003"].message
    assert "domainIncludes" in rows["CNF-003"].remediation
    assert rows["CNF-003"].path == str(EX.hasCarriageway)


def test_an_includes_annotation_does_not_declare_the_property():
    """The deliberate asymmetry with rdfs:domain, which does.

    Anything carrying an `rdfs:domain` *is* an `rdf:Property` by RDFS
    entailment, so the loops that read those add to `declared_properties`.
    `domainIncludes` is an annotation `rdfs:subPropertyOf skos:scopeNote`; it
    entails nothing whatever about its subject. A property described only by a
    scope note is still undeclared, and CNF-002 must go on saying so.
    """
    ontology = rdflib.Graph().parse(data="""
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .
        @prefix gist: <https://w3id.org/semanticarts/ns/ontology/gist/> .
        @prefix ex:   <https://example.org/g/> .
        ex:Road a owl:Class .
        ex:undeclared gist:domainIncludes ex:Road .
    """, format="turtle")
    declarations = dq.ontology_declarations(ontology)
    assert EX.undeclared not in declarations["properties"]
    assert declarations["domain"][EX.undeclared] == {EX.Road}

    data = rdflib.Graph().parse(data="""
        @prefix ex: <https://example.org/g/> .
        ex:r1 a ex:Road ; ex:undeclared ex:r1 .
    """, format="turtle")
    conf = dq.check_conformance(declarations, data)
    assert EX.undeclared in conf["undeclared_properties_used"]
