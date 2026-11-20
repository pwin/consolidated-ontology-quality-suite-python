"""CNF-003/CNF-004 accept a domain or range declared as an owl:unionOf.

A union is written as an anonymous class node::

    ex:locatedIn rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Site ex:Region ) ] .

and `check_conformance` decides whether a subject satisfies a declared domain
by walking `rdfs:subClassOf` from each of its asserted types. No asserted type
can ever reach a blank node that way, so the comparison could only ever come
out "no match" -- and *every* use of a union-domained property was reported as
a violation, every value of a union-ranged one likewise. Measured on the
three-triple graph below before the fix: 5 violations, all false.

The reason this went unnoticed is worth keeping. It is not a corner case:
gist, which this suite ships several versions of as a reference vocabulary,
declares 13 anonymous domains and ranges and every single one is a union; so
are all three in `examples/acme_robotics`'s copy of the W3C org vocabulary, on
`org:reportsTo` and `org:role`. The acme fixture carries the bug in its own
reference vocab and has done all along -- it just happens that the acme data
never uses those two properties, so nothing fired. The defect surfaced on a
different project's data, against `locatedIn` and `founded`.

The second test is the one that matters most. A fix that simply stopped
reporting would be worse than the bug, because a clean report and a correct
ontology look identical -- the failure mode `test_check_firing_coverage.py`
exists to catch. So a subject and a value in *neither* union member must still
be caught.

The last test pins what is deliberately not expanded. An `owl:intersectionOf`
means "all of these", so flattening it to alternatives would make the check
unsound in the direction that matters: it would start accepting data the
ontology forbids. Those stay anonymous, and `satisfies` declines to judge
them rather than calling them violations -- "no verdict" is the honest answer
and it is what the unverifiable counters already exist for.
"""
import rdflib

from ontology_suite.dataquality import data_quality as dq

PREFIXES = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <https://example.org/trail/> .
"""

ONTOLOGY = PREFIXES + """
ex:Site a owl:Class . ex:Region a owl:Class . ex:Era a owl:Class . ex:Person a owl:Class .

# The pattern org.ttl uses for org:reportsTo: a union on both sides.
ex:locatedIn a owl:ObjectProperty ;
  rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Site ex:Region ) ] ;
  rdfs:range  [ a owl:Class ; owl:unionOf ( ex:Region ex:Era ) ] .

# A union nested inside a union, to pin that the expansion recurses rather
# than flattening one level and leaving a blank node behind.
ex:founded a owl:ObjectProperty ;
  rdfs:domain [ a owl:Class ; owl:unionOf ( ex:Site [ a owl:Class ; owl:unionOf ( ex:Region ex:Era ) ] ) ] ;
  rdfs:range  ex:Era .
"""


def _declarations(ontology_ttl=ONTOLOGY):
    return dq.ontology_declarations(rdflib.Graph().parse(data=ontology_ttl, format="turtle"))


def _cnf_rows(data_ttl, ontology_ttl=ONTOLOGY):
    data = rdflib.Graph().parse(data=PREFIXES + data_ttl, format="turtle")
    conformance = dq.check_conformance(_declarations(ontology_ttl), data)
    return [
        row for row in dq.conformance_to_rows(conformance, "data")
        if row.check_id in ("CNF-003", "CNF-004")
    ]


EX = rdflib.Namespace("https://example.org/trail/")


def test_a_union_is_collected_as_its_member_classes():
    """The fix is in the collection, not in the comparison: by the time
    `check_conformance` sees a domain it is a set of named classes."""
    declarations = _declarations()
    assert declarations["domain"][EX.locatedIn] == {EX.Site, EX.Region}
    assert declarations["range"][EX.locatedIn] == {EX.Region, EX.Era}
    assert not any(
        isinstance(c, rdflib.BNode)
        for table in ("domain", "range")
        for classes in declarations[table].values()
        for c in classes
    ), "a union was left as an anonymous node, which nothing can ever match"


def test_a_nested_union_is_flattened_all_the_way():
    assert _declarations()["domain"][EX.founded] == {EX.Site, EX.Region, EX.Era}


def test_satisfying_one_member_of_a_union_is_not_a_violation():
    """Both members, on both sides, and through the nested union."""
    rows = _cnf_rows("""
        ex:abbey     a ex:Site   ; ex:locatedIn ex:northVale ; ex:founded ex:medieval .
        ex:northVale a ex:Region ; ex:locatedIn ex:medieval .
        ex:medieval  a ex:Era .
    """)
    assert rows == [], f"union members reported as violations: {[(r.check_id, r.focus_node) for r in rows]}"


def test_matching_no_member_of_a_union_is_still_a_violation():
    """The half that makes the fix worth having. ex:Person is in neither
    union, as subject or as value."""
    rows = _cnf_rows("""
        ex:ada  a ex:Person ; ex:locatedIn ex:bob .
        ex:bob  a ex:Person .
    """)
    reported = {(r.check_id, r.focus_node) for r in rows}
    assert reported == {
        ("CNF-003", str(EX.ada)),   # subject in neither ex:Site nor ex:Region
        ("CNF-004", str(EX.bob)),   # value   in neither ex:Region nor ex:Era
    }, reported


def test_a_subclass_of_a_union_member_satisfies_it():
    """The union is expanded before the ancestor walk, not instead of it."""
    ontology = ONTOLOGY + "\nex:Priory rdfs:subClassOf ex:Site .\n"
    rows = _cnf_rows("""
        ex:abbey     a ex:Priory ; ex:locatedIn ex:northVale .
        ex:northVale a ex:Region .
    """, ontology_ttl=ontology)
    assert rows == []


def test_an_intersection_is_not_expanded_and_is_not_a_violation_either():
    """Neither flattened (which would be unsound) nor reported (which was the
    bug). It yields no verdict and lands in the unverifiable count."""
    ontology = PREFIXES + """
    ex:Site a owl:Class . ex:Region a owl:Class . ex:Person a owl:Class .
    ex:locatedIn a owl:ObjectProperty ;
      rdfs:domain [ a owl:Class ; owl:intersectionOf ( ex:Site ex:Region ) ] .
    """
    declarations = _declarations(ontology)
    declared = declarations["domain"][EX.locatedIn]
    assert len(declared) == 1 and isinstance(next(iter(declared)), rdflib.BNode), declared

    data = rdflib.Graph().parse(data=PREFIXES + """
        ex:ada a ex:Person ; ex:locatedIn ex:somewhere .
    """, format="turtle")
    conformance = dq.check_conformance(declarations, data)
    assert conformance["domain_violations"] == {}
    assert conformance["unverifiable_domain"].get(EX.locatedIn) == 1


def test_gist_style_domain_includes_also_expands():
    """`domainIncludes` is read by local name, and may carry a union too."""
    ontology = PREFIXES + """
    @prefix gist: <https://ontologies.semanticarts.com/gist/> .
    ex:Site a owl:Class . ex:Region a owl:Class .
    ex:locatedIn a owl:ObjectProperty ;
      gist:domainIncludes [ a owl:Class ; owl:unionOf ( ex:Site ex:Region ) ] .
    """
    assert _declarations(ontology)["domain"][EX.locatedIn] == {EX.Site, EX.Region}
