"""Small regression suite for a specific real-world scenario the standalone
prefix/undeclared-term checks in ontology_suite.sketch.prefix_alignment
exist to catch: an ontology evolves from one version to the next (classes/
properties renamed, a namespace IRI bumped), and a TARQL transform file
needs to be checked for whether it still matches -- and if not, exactly
which declarations it needs to be updated with.

Two independent kinds of version drift are exercised, matching the two
checks the module provides:

1. Vocabulary rename (examples/-style `ex:Widget`/`ex:price` -> `ex:Product`/
   `ex:cost`, plus a wholly new `ex:sku`) -- surfaces as undeclared classes/
   properties (check_undeclared_terms).
2. Namespace IRI bump on an unchanged prefix name (the gist-style versioned-
   namespace pattern noted in this suite's own examples/gist_versions_reference/)
   -- surfaces as a namespace_mismatch (check_tarql_ontology_prefix_alignment).
"""
import pytest

from ontology_suite.sketch import prefix_alignment as pa


# --- fixtures: two ontology versions, two transform versions ---------------

@pytest.fixture(scope="module")
def rename_fixtures(tmp_path_factory):
    """v1 declares ex:Widget/ex:price; v2 renames them to ex:Product/ex:cost
    and adds a new ex:sku -- the same namespace IRI throughout, only the
    local names change (a vocabulary-rename version bump, not a namespace
    bump)."""
    d = tmp_path_factory.mktemp("rename_fixtures")

    onto_v1 = d / "domain-v1.ttl"
    onto_v1.write_text(
        "@prefix ex: <https://example.org/demo/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        'ex:DemoOntology a owl:Ontology ; owl:versionInfo "1.0.0" .\n'
        'ex:Widget a owl:Class ; rdfs:label "Widget" .\n'
        "ex:price a owl:DatatypeProperty ; rdfs:domain ex:Widget .\n",
        encoding="utf-8",
    )

    onto_v2 = d / "domain-v2.ttl"
    onto_v2.write_text(
        "@prefix ex: <https://example.org/demo/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        'ex:DemoOntology a owl:Ontology ; owl:versionInfo "2.0.0" .\n'
        'ex:Product a owl:Class ; rdfs:label "Product" .\n'
        "ex:cost a owl:DatatypeProperty ; rdfs:domain ex:Product .\n"
        "ex:sku a owl:DatatypeProperty ; rdfs:domain ex:Product .\n",
        encoding="utf-8",
    )

    tarql_v1 = d / "transform-v1.rq"
    tarql_v1.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT {\n"
        "  ?item a ex:Widget ;\n"
        "    ex:price ?p .\n"
        "} WHERE {\n"
        '  BIND(IRI(CONCAT("https://example.org/demo/item-", ?id)) AS ?item)\n'
        "}\n",
        encoding="utf-8",
    )

    tarql_v2 = d / "transform-v2.rq"
    tarql_v2.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT {\n"
        "  ?item a ex:Product ;\n"
        "    ex:cost ?p ;\n"
        "    ex:sku ?s .\n"
        "} WHERE {\n"
        '  BIND(IRI(CONCAT("https://example.org/demo/item-", ?id)) AS ?item)\n'
        "}\n",
        encoding="utf-8",
    )

    return {"onto_v1": onto_v1, "onto_v2": onto_v2, "tarql_v1": tarql_v1, "tarql_v2": tarql_v2}


@pytest.fixture(scope="module")
def namespace_bump_fixtures(tmp_path_factory):
    """v2 bumps the ontology's own namespace IRI (the gist-style versioned-
    IRI pattern: gistCore14.0.0 -> gistCore14.1.0), keeping the same `ex:`
    prefix label and the same local names -- a transform still pointing at
    the v1 namespace should be flagged as a namespace_mismatch, not an
    undeclared term (the terms exist, just under the old IRI)."""
    d = tmp_path_factory.mktemp("namespace_bump_fixtures")

    onto_v1 = d / "domain-v1.ttl"
    onto_v1.write_text(
        "@prefix ex: <https://example.org/demo/v1/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        'ex:DemoOntology a owl:Ontology ; owl:versionInfo "1.0.0" .\n'
        "ex:Widget a owl:Class .\n",
        encoding="utf-8",
    )

    onto_v2 = d / "domain-v2.ttl"
    onto_v2.write_text(
        "@prefix ex: <https://example.org/demo/v2/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        'ex:DemoOntology a owl:Ontology ; owl:versionInfo "2.0.0" .\n'
        "ex:Widget a owl:Class .\n",
        encoding="utf-8",
    )

    tarql_v1 = d / "transform-v1.rq"
    tarql_v1.write_text(
        "PREFIX ex: <https://example.org/demo/v1/>\n"
        "CONSTRUCT { ?item a ex:Widget . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )

    tarql_v2 = d / "transform-v2.rq"
    tarql_v2.write_text(
        "PREFIX ex: <https://example.org/demo/v2/>\n"
        "CONSTRUCT { ?item a ex:Widget . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )

    return {"onto_v1": onto_v1, "onto_v2": onto_v2, "tarql_v1": tarql_v1, "tarql_v2": tarql_v2}


# --- vocabulary rename: old transform vs. new ontology ---------------------

def test_v1_transform_is_aligned_with_v1_ontology(rename_fixtures):
    f = rename_fixtures
    report = pa.check_tarql_ontology_alignment([f["tarql_v1"]], [f["onto_v1"]])
    assert report.is_clean


def test_v2_transform_is_aligned_with_v2_ontology(rename_fixtures):
    """The already-updated transform should need no further changes."""
    f = rename_fixtures
    report = pa.check_tarql_ontology_alignment([f["tarql_v2"]], [f["onto_v2"]])
    assert report.is_clean


def test_v1_transform_against_v2_ontology_identifies_exactly_the_renamed_terms(rename_fixtures):
    """The core scenario: an un-updated transform checked against the new
    ontology version should name precisely the two terms that need to
    change (the old class and property local names), with no unrelated
    noise -- this is the "what do I need to fix" signal, not just a
    pass/fail flag."""
    f = rename_fixtures
    findings = pa.check_undeclared_terms([f["tarql_v1"]], [f["onto_v2"]])
    reported = {(t.kind, t.term) for t in findings}
    assert reported == {
        ("class", "https://example.org/demo/Widget"),
        ("property", "https://example.org/demo/price"),
    }


def test_updating_the_transform_to_v2_resolves_the_misalignment(rename_fixtures):
    """Swapping in the v2 transform (the fix a human would make in response
    to the previous test's findings) against the same v2 ontology should
    leave nothing outstanding."""
    f = rename_fixtures
    report = pa.check_tarql_ontology_alignment([f["tarql_v2"]], [f["onto_v2"]])
    assert report.is_clean


# --- namespace IRI bump: old transform vs. new ontology --------------------

def test_v1_transform_against_v2_ontology_is_a_namespace_mismatch(namespace_bump_fixtures):
    """When only the namespace IRI moved (terms otherwise unchanged), the
    prefix-level check correctly diagnoses "wrong namespace" -- the fix is
    a one-line PREFIX change, not adding a new class -- while the term-level
    check, working purely off IRI identity, independently flags the same
    v1-namespaced class as undeclared against the v2 ontology (since
    `.../v1#Widget` and `.../v2#Widget` are, correctly, different IRIs).
    Both signals point at the same root cause and the same fix."""
    f = namespace_bump_fixtures
    report = pa.check_tarql_ontology_alignment([f["tarql_v1"]], [f["onto_v2"]])

    assert len(report.prefix_misalignments) == 1
    assert report.prefix_misalignments[0].kind == "namespace_mismatch"
    assert report.prefix_misalignments[0].tarql_namespace == "https://example.org/demo/v1/"
    assert "https://example.org/demo/v2/" in report.prefix_misalignments[0].detail

    assert len(report.undeclared_terms) == 1
    assert report.undeclared_terms[0].kind == "class"
    assert report.undeclared_terms[0].term == "https://example.org/demo/v1/Widget"


def test_v2_transform_is_aligned_with_v2_ontology_namespace(namespace_bump_fixtures):
    f = namespace_bump_fixtures
    report = pa.check_tarql_ontology_alignment([f["tarql_v2"]], [f["onto_v2"]])
    assert report.is_clean
