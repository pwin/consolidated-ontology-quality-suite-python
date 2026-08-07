"""Extends the version-alignment scenario in test_tarql_ontology_version_alignment.py
with a review of *real triplified output data*, not just the static
CONSTRUCT-template/sketch analysis: actually runs each transform version's
query against a real CSV through the real ``oxi-gen`` binary (the same
engine ``pipeline.run_triplify_stage``/the ``triplify`` CLI stage shells out
to), then checks the genuine resulting RDF against each ontology version
via ``dataquality.data_quality``'s conformance logic -- the same check the
``data`` pipeline stage's CNF-001/CNF-002 findings come from.

This is a materially different, stronger signal than the static checks:
it proves the *actual output a real run would produce* conforms (or
doesn't), not just that the query text mentions the right vocabulary --
catching things like a CSV column binding to a literal of an unexpected
form that the template-only view can't see.

Requires a built ``oxi-gen`` binary (sibling checkout,
``cargo build --release``) -- skipped entirely if one isn't found, the same
way this suite's other optional-external-tool tests are.
"""
from pathlib import Path

import pytest
import rdflib

from ontology_suite import config
from ontology_suite.dataquality import data_quality
from ontology_suite.triplify import oxigen
from ontology_suite.triplify.discovery import TriplifyJob

OXI_GEN_BIN = config.find_oxi_gen_binary()
pytestmark = pytest.mark.skipif(
    OXI_GEN_BIN is None,
    reason="oxi-gen binary not built (cargo build --release in the sibling oxi-gen checkout)",
)

EX = "https://example.org/demo/"


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def output_data_fixtures(tmp_path_factory):
    """Same vocabulary-rename scenario as test_tarql_ontology_version_alignment.py's
    rename_fixtures (ex:Widget/ex:price -> ex:Product/ex:cost, + new
    ex:sku), plus a small CSV for each transform version so it can actually
    be run through oxi-gen rather than only inspected as text."""
    d = tmp_path_factory.mktemp("output_data_fixtures")

    onto_v1 = _write(
        d / "domain-v1.ttl",
        f"@prefix ex: <{EX}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f'@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        f'ex:DemoOntology a owl:Ontology ; owl:versionInfo "1.0.0" .\n'
        f'ex:Widget a owl:Class ; rdfs:label "Widget" .\n'
        f"ex:price a owl:DatatypeProperty ; rdfs:domain ex:Widget .\n",
    )
    onto_v2 = _write(
        d / "domain-v2.ttl",
        f"@prefix ex: <{EX}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f'@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        f'ex:DemoOntology a owl:Ontology ; owl:versionInfo "2.0.0" .\n'
        f'ex:Product a owl:Class ; rdfs:label "Product" .\n'
        f"ex:cost a owl:DatatypeProperty ; rdfs:domain ex:Product .\n"
        f"ex:sku a owl:DatatypeProperty ; rdfs:domain ex:Product .\n",
    )

    transform_v1 = _write(
        d / "transform-v1.rq",
        f"PREFIX ex: <{EX}>\n"
        "CONSTRUCT {\n"
        "  ?item a ex:Widget ;\n"
        "    ex:price ?p .\n"
        "} WHERE {\n"
        f'  BIND(IRI(CONCAT("{EX}item-", ?id)) AS ?item)\n'
        "}\n",
    )
    items_v1 = _write(d / "items-v1.csv", "id,p\n1,9.99\n2,14.50\n")

    transform_v2 = _write(
        d / "transform-v2.rq",
        f"PREFIX ex: <{EX}>\n"
        "CONSTRUCT {\n"
        "  ?item a ex:Product ;\n"
        "    ex:cost ?p ;\n"
        "    ex:sku ?sku .\n"
        "} WHERE {\n"
        f'  BIND(IRI(CONCAT("{EX}item-", ?id)) AS ?item)\n'
        "}\n",
    )
    items_v2 = _write(d / "items-v2.csv", "id,p,sku\n1,9.99,SKU-1\n2,14.50,SKU-2\n")

    output_v1 = d / "output-v1.ttl"
    oxigen.run_oxi_gen(OXI_GEN_BIN, TriplifyJob(csv_path=items_v1, query_path=transform_v1), output_v1)
    output_v2 = d / "output-v2.ttl"
    oxigen.run_oxi_gen(OXI_GEN_BIN, TriplifyJob(csv_path=items_v2, query_path=transform_v2), output_v2)

    return {"onto_v1": onto_v1, "onto_v2": onto_v2, "output_v1": output_v1, "output_v2": output_v2}


def _load(path: Path) -> rdflib.Graph:
    graph = rdflib.Graph(bind_namespaces="none")
    graph.parse(str(path), format="turtle")
    return graph


def _conformance(output_path, ontology_path):
    declarations = data_quality.ontology_declarations(_load(ontology_path))
    return data_quality.check_conformance(declarations, _load(output_path))


def test_v1_output_data_was_actually_triplified(output_data_fixtures):
    """Sanity check that this is reviewing real oxi-gen output, not a mock:
    the expected real triples (from real CSV values) are actually there."""
    graph = _load(output_data_fixtures["output_v1"])
    widget1 = rdflib.URIRef(f"{EX}item-1")
    assert (widget1, rdflib.RDF.type, rdflib.URIRef(f"{EX}Widget")) in graph
    assert (widget1, rdflib.URIRef(f"{EX}price"), rdflib.Literal("9.99")) in graph
    assert len(graph) == 4  # 2 items x (rdf:type + price)


def test_v1_output_data_conforms_to_v1_ontology(output_data_fixtures):
    f = output_data_fixtures
    conformance = _conformance(f["output_v1"], f["onto_v1"])
    assert conformance["undeclared_classes_used"] == set()
    assert conformance["undeclared_properties_used"] == set()


def test_v2_output_data_conforms_to_v2_ontology(output_data_fixtures):
    f = output_data_fixtures
    conformance = _conformance(f["output_v2"], f["onto_v2"])
    assert conformance["undeclared_classes_used"] == set()
    assert conformance["undeclared_properties_used"] == set()


def test_v1_output_data_is_misaligned_with_v2_ontology(output_data_fixtures):
    """The real-data equivalent of the static-analysis finding: data
    actually triplified with the old transform, reviewed against the new
    ontology, is missing exactly the renamed class and property -- proving
    the drift is real, not just a text-level artifact of the query."""
    f = output_data_fixtures
    conformance = _conformance(f["output_v1"], f["onto_v2"])
    assert conformance["undeclared_classes_used"] == {rdflib.URIRef(f"{EX}Widget")}
    assert conformance["undeclared_properties_used"] == {rdflib.URIRef(f"{EX}price")}


def test_v2_output_data_is_misaligned_with_v1_ontology(output_data_fixtures):
    """Symmetric check: data from the *new* transform doesn't conform to
    the *old* ontology either (ex:Product/ex:cost/ex:sku are all new)."""
    f = output_data_fixtures
    conformance = _conformance(f["output_v2"], f["onto_v1"])
    assert conformance["undeclared_classes_used"] == {rdflib.URIRef(f"{EX}Product")}
    assert conformance["undeclared_properties_used"] == {
        rdflib.URIRef(f"{EX}cost"), rdflib.URIRef(f"{EX}sku"),
    }
