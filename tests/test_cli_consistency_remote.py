"""Covers the `consistency-remote` CLI subcommand itself (arg parsing,
auth-tuple construction, per-graph report writing, exit-code logic) --
previously only the library functions it calls (`remote.fuseki`,
`remote.manifest.check_manifest_consistency`) were tested, via
`test_fuseki.py`/`test_remote_manifest.py`, leaving `cmd_consistency_remote`
itself as the one CLI-layer path exercised only manually. Reuses the same
fake-SPARQL-endpoint fixture (`fuseki_server`, in conftest.py) those tests
already use.
"""
import rdflib

from ontology_suite import cli
from ontology_suite.remote import fuseki
from ontology_suite.remote.manifest import GraphBinding, GraphManifest

EX = "https://example.org/demo/"
ONTOLOGY_GRAPH = "https://example.org/graphs/ontology"
DATA_GRAPH = "https://example.org/graphs/data"


def _seed_clean(dataset, tarql_path):
    ontology_graph = dataset.get_context(rdflib.URIRef(ONTOLOGY_GRAPH))
    ontology_graph.parse(
        data=f"@prefix ex: <{EX}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Animal a owl:Class .\n",
        format="turtle",
    )
    tarql_path.write_text(
        f"PREFIX ex: <{EX}>\nCONSTRUCT {{ ?a a ex:Animal . }} WHERE {{ BIND(?x AS ?a) }}\n", encoding="utf-8"
    )
    data_graph = dataset.get_context(rdflib.URIRef(DATA_GRAPH))
    data_graph.add((rdflib.URIRef(EX + "item-1"), rdflib.RDF.type, rdflib.URIRef(EX + "Animal")))


def _write_manifest(tmp_path, tarql_path):
    manifest = GraphManifest(bindings=[
        GraphBinding(graph_uri=ONTOLOGY_GRAPH, role="ontology"),
        GraphBinding(
            graph_uri=DATA_GRAPH, role="triplified_data",
            source_tarql=str(tarql_path), ontology_graph_uri=ONTOLOGY_GRAPH,
        ),
    ])
    path = tmp_path / "manifest.json"
    manifest.save(path)
    return path


def test_consistency_remote_clean_manifest_exits_zero_and_writes_report(fuseki_server, tmp_path):
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed_clean(dataset, tarql_path)
    manifest_path = _write_manifest(tmp_path, tarql_path)
    out_dir = tmp_path / "out"

    exit_code = cli.main([
        "consistency-remote",
        "--query-endpoint", query_url,
        "--manifest", str(manifest_path),
        "--out-dir", str(out_dir),
    ])

    assert exit_code == 0
    safe_name = DATA_GRAPH.replace("://", "_").replace("/", "_")
    report_path = out_dir / f"{safe_name}.txt"
    assert report_path.is_file()
    assert "template vs ontology: clean" in report_path.read_text(encoding="utf-8")


def test_consistency_remote_fail_on_misalignment_exits_one_on_a_real_gap(fuseki_server, tmp_path):
    """Mirrors test_remote_manifest.py's test_live_data_diverges_from_ontology
    at the CLI layer: the live named graph has a class the ontology never
    declares -- --fail-on-misalignment should surface that as a non-zero
    exit code; without the flag, the same gap is report-only (exit 0)."""
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed_clean(dataset, tarql_path)
    dataset.get_context(rdflib.URIRef(DATA_GRAPH)).add(
        (rdflib.URIRef(EX + "item-2"), rdflib.RDF.type, rdflib.URIRef(EX + "Mystery"))
    )
    manifest_path = _write_manifest(tmp_path, tarql_path)

    exit_report_only = cli.main([
        "consistency-remote", "--query-endpoint", query_url, "--manifest", str(manifest_path),
        "--out-dir", str(tmp_path / "out-report-only"),
    ])
    assert exit_report_only == 0

    exit_strict = cli.main([
        "consistency-remote", "--query-endpoint", query_url, "--manifest", str(manifest_path),
        "--out-dir", str(tmp_path / "out-strict"), "--fail-on-misalignment",
    ])
    assert exit_strict == 1


def test_consistency_remote_constructs_auth_tuple_from_cli_args(fuseki_server, tmp_path, monkeypatch):
    """--auth-user/--auth-password should reach FusekiDataset as an
    (user, password) tuple -- the one piece of cmd_consistency_remote's own
    logic (auth = (args.auth_user, args.auth_password) if args.auth_user
    else None) that isn't exercised just by calling the endpoint."""
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed_clean(dataset, tarql_path)
    manifest_path = _write_manifest(tmp_path, tarql_path)

    captured = {}
    real_init = fuseki.FusekiDataset.__init__

    def spy_init(self, *args, **kwargs):
        captured["auth"] = kwargs.get("auth")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(fuseki.FusekiDataset, "__init__", spy_init)

    exit_code = cli.main([
        "consistency-remote",
        "--query-endpoint", query_url,
        "--manifest", str(manifest_path),
        "--out-dir", str(tmp_path / "out"),
        "--auth-user", "alice",
        "--auth-password", "secret",
    ])

    assert exit_code == 0
    assert captured["auth"] == ("alice", "secret")


def test_consistency_remote_no_auth_user_means_auth_is_none(fuseki_server, tmp_path, monkeypatch):
    query_url, _update_url, dataset, _server = fuseki_server
    tarql_path = tmp_path / "transform.rq"
    _seed_clean(dataset, tarql_path)
    manifest_path = _write_manifest(tmp_path, tarql_path)

    captured = {}
    real_init = fuseki.FusekiDataset.__init__

    def spy_init(self, *args, **kwargs):
        captured["auth"] = kwargs.get("auth")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(fuseki.FusekiDataset, "__init__", spy_init)

    exit_code = cli.main([
        "consistency-remote",
        "--query-endpoint", query_url,
        "--manifest", str(manifest_path),
        "--out-dir", str(tmp_path / "out"),
    ])

    assert exit_code == 0
    assert captured["auth"] is None
