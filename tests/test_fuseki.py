"""Covers remote.fuseki against the in-process fake SPARQL endpoint in
conftest.py: query-form detection, named-graph listing/materializing,
default-graph-uri scoping (the mechanism that lets the *existing*,
unmodified registry .rq checks run against just the intended named
graph(s)), and remote update application.
"""
from pathlib import Path

import pytest
import rdflib

from ontology_suite.remote import fuseki

EX = "https://example.org/"


def test_detect_query_form():
    assert fuseki.detect_query_form("SELECT * WHERE { ?s ?p ?o }") == "SELECT"
    assert fuseki.detect_query_form("PREFIX ex: <https://example.org/>\nCONSTRUCT { ?s ?p ?o } WHERE {}") == "CONSTRUCT"
    assert fuseki.detect_query_form("ASK { ?s ?p ?o }") == "ASK"
    with pytest.raises(fuseki.FusekiError):
        fuseki.detect_query_form("not a query")


def test_list_named_graphs(fuseki_server):
    query_url, update_url, dataset, server = fuseki_server
    dataset.get_context(rdflib.URIRef(EX + "g1")).add(
        (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    dataset.get_context(rdflib.URIRef(EX + "g2")).add(
        (rdflib.URIRef(EX + "b"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    graphs = fuseki.list_named_graphs(ds)
    assert graphs == [EX + "g1", EX + "g2"]


def test_load_named_graph_materializes_only_that_graph(fuseki_server):
    query_url, _update_url, dataset, _server = fuseki_server
    dataset.get_context(rdflib.URIRef(EX + "g1")).add(
        (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    dataset.get_context(rdflib.URIRef(EX + "g2")).add(
        (rdflib.URIRef(EX + "b"), rdflib.RDF.type, rdflib.URIRef(EX + "OtherThing"))
    )
    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    g = fuseki.load_named_graph(ds, EX + "g1")
    assert len(g) == 1
    assert (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing")) in g


def test_graph_predicate_and_type_usage(fuseki_server):
    query_url, _update_url, dataset, _server = fuseki_server
    g = dataset.get_context(rdflib.URIRef(EX + "g1"))
    g.add((rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing")))
    g.add((rdflib.URIRef(EX + "a"), rdflib.URIRef(EX + "hasName"), rdflib.Literal("A")))
    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    usage = fuseki.graph_predicate_and_type_usage(ds, EX + "g1")
    assert usage["classes"] == [EX + "Thing"]
    assert set(usage["properties"]) == {"http://www.w3.org/1999/02/22-rdf-syntax-ns#type", EX + "hasName"}


def test_run_query_scopes_to_default_graph_uris(fuseki_server):
    """A query with no GRAPH clause, scoped via default-graph-uri to only
    one of two named graphs, must see only that graph's triples -- this is
    the mechanism run_registry_checks_remote depends on."""
    query_url, _update_url, dataset, _server = fuseki_server
    dataset.get_context(rdflib.URIRef(EX + "g1")).add(
        (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    dataset.get_context(rdflib.URIRef(EX + "g2")).add(
        (rdflib.URIRef(EX + "b"), rdflib.RDF.type, rdflib.URIRef(EX + "OtherThing"))
    )
    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    result = fuseki.run_query(ds, "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", default_graph_uris=[EX + "g1"])
    assert result.graph is not None
    assert len(result.graph) == 1
    assert (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing")) in result.graph


def test_run_registry_checks_remote(fuseki_server, tmp_path):
    query_url, _update_url, dataset, _server = fuseki_server
    dataset.get_context(rdflib.URIRef(EX + "data")).add(
        (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    sparql_dir = tmp_path / "sparql"
    sparql_dir.mkdir()
    (sparql_dir / "TEST-001.rq").write_text(
        "CONSTRUCT { ?s <https://example.org/isThing> true } WHERE { ?s a <https://example.org/Thing> }",
        encoding="utf-8",
    )
    ds = fuseki.FusekiDataset(query_endpoint=query_url)
    results, outcomes = fuseki.run_registry_checks_remote(ds, sparql_dir, default_graph_uris=[EX + "data"])
    assert len(outcomes) == 1
    assert outcomes[0].ok
    assert outcomes[0].result_count == 1
    assert len(results) == 1


def test_apply_repair_remote(fuseki_server):
    query_url, update_url, dataset, server = fuseki_server
    dataset.get_context(rdflib.URIRef(EX + "g1")).add(
        (rdflib.URIRef(EX + "a"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing"))
    )
    ds = fuseki.FusekiDataset(query_endpoint=query_url, update_endpoint=update_url)
    fuseki.apply_repair_remote(
        ds,
        f"INSERT DATA {{ GRAPH <{EX}g1> {{ <{EX}b> a <{EX}Thing2> }} }}",
    )
    g = dataset.get_context(rdflib.URIRef(EX + "g1"))
    assert (rdflib.URIRef(EX + "b"), rdflib.RDF.type, rdflib.URIRef(EX + "Thing2")) in g


def test_run_update_without_endpoint_raises():
    ds = fuseki.FusekiDataset(query_endpoint="https://example.org/query")
    with pytest.raises(fuseki.FusekiError):
        fuseki.run_update(ds, "INSERT DATA { <https://example.org/a> a <https://example.org/Thing> }")
