"""Covers remote.manifest: the GraphManifest provenance binding, and the
three-way named-graph consistency check (template-vs-ontology,
live-data-vs-ontology, template-vs-live-data) against the fake SPARQL
endpoint in conftest.py.
"""
import json

import rdflib

from ontology_suite.remote import fuseki
from ontology_suite.remote.manifest import GraphBinding, GraphManifest, check_named_graph_consistency

EX = "https://example.org/demo/"
ONTOLOGY_GRAPH = "https://example.org/graphs/ontology"
DATA_GRAPH = "https://example.org/graphs/data"


def _seed(dataset, tarql_path):
    ontology_graph = dataset.get_context(rdflib.URIRef(ONTOLOGY_GRAPH))
    ontology_graph.parse(
        data=f"@prefix ex: <{EX}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Animal a owl:Class .\n",
        format="turtle",
    )
    tarql_path.write_text(
        f"PREFIX ex: <{EX}>\nCONSTRUCT {{ ?a a ex:Animal . }} WHERE {{ BIND(?x AS ?a) }}\n", encoding="utf-8"
    )


def test_manifest_round_trips_through_json(tmp_path):
    manifest = GraphManifest(bindings=[
        GraphBinding(graph_uri=ONTOLOGY_GRAPH, role="ontology"),
        GraphBinding(graph_uri=DATA_GRAPH, role="triplified_data", source_tarql="transform.rq", ontology_graph_uri=ONTOLOGY_GRAPH),
    ])
    path = tmp_path / "graphs.json"
    manifest.save(path)
    loaded = GraphManifest.load(path)
    assert len(loaded.bindings) == 2
    assert loaded.ontology_bindings()[0].graph_uri == ONTOLOGY_GRAPH
    assert loaded.data_bindings()[0].source_tarql == "transform.rq"


def test_clean_named_graph_reports_no_issues(fuseki_server, tmp_path):
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed(dataset, tarql_path)
    data_graph = dataset.get_context(rdflib.URIRef(DATA_GRAPH))
    data_graph.add((rdflib.URIRef(EX + "item-1"), rdflib.RDF.type, rdflib.URIRef(EX + "Animal")))

    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    binding = GraphBinding(graph_uri=DATA_GRAPH, role="triplified_data", source_tarql=str(tarql_path), ontology_graph_uri=ONTOLOGY_GRAPH)
    report = check_named_graph_consistency(ds, binding)

    assert report.warnings == []
    assert report.template_vs_ontology == []
    assert report.live_data_vs_ontology is not None
    assert report.live_data_vs_ontology["undeclared_classes_used"] == set()
    assert report.template_vs_live_data == {
        "classes_only_in_template": [], "classes_only_in_live_data": [],
        "properties_only_in_template": [], "properties_only_in_live_data": [],
    }
    assert report.is_clean


def test_live_data_diverges_from_ontology(fuseki_server, tmp_path):
    """The live named graph has a class the ontology never declares -- the
    static template check wouldn't catch this (the template never mentions
    it), only the live-data-vs-ontology check does."""
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed(dataset, tarql_path)
    data_graph = dataset.get_context(rdflib.URIRef(DATA_GRAPH))
    data_graph.add((rdflib.URIRef(EX + "item-1"), rdflib.RDF.type, rdflib.URIRef(EX + "Animal")))
    data_graph.add((rdflib.URIRef(EX + "item-2"), rdflib.RDF.type, rdflib.URIRef(EX + "Mystery")))

    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    binding = GraphBinding(graph_uri=DATA_GRAPH, role="triplified_data", source_tarql=str(tarql_path), ontology_graph_uri=ONTOLOGY_GRAPH)
    report = check_named_graph_consistency(ds, binding)

    assert report.template_vs_ontology == []  # the template itself is fine
    assert report.live_data_vs_ontology is not None
    assert rdflib.URIRef(EX + "Mystery") in report.live_data_vs_ontology["undeclared_classes_used"]
    assert not report.is_clean


def test_template_diverges_from_live_data(fuseki_server, tmp_path):
    """The query template promises ex:Animal but the named graph was never
    (re-)triplified with it -- an execution-pipeline problem, not a
    modelling one; only the template-vs-live-data check catches this."""
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed(dataset, tarql_path)
    # data graph left empty -- simulates a stale/never-run triplify job

    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    binding = GraphBinding(graph_uri=DATA_GRAPH, role="triplified_data", source_tarql=str(tarql_path), ontology_graph_uri=ONTOLOGY_GRAPH)
    report = check_named_graph_consistency(ds, binding)

    assert report.template_vs_live_data is not None
    assert EX + "Animal" in report.template_vs_live_data["classes_only_in_template"]
    assert not report.is_clean


def test_missing_ontology_binding_produces_a_warning_and_skips_ontology_checks(fuseki_server, tmp_path):
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed(dataset, tarql_path)

    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    binding = GraphBinding(graph_uri=DATA_GRAPH, role="triplified_data", source_tarql=str(tarql_path))
    report = check_named_graph_consistency(ds, binding)

    assert any("ontology_graph_uri" in w for w in report.warnings)
    assert report.template_vs_ontology is None
    assert report.live_data_vs_ontology is None
