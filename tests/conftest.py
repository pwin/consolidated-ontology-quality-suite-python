"""Shared fixtures for the remote/Fuseki tests: an in-process HTTP server
implementing just enough of the SPARQL 1.1 Protocol to exercise
remote.fuseki's real urllib request/response code path (no mocking of
urllib itself, and no dependency on a real Fuseki instance -- the graphs
served are a tiny fixed dataset, held in module state and reset per test
via the `graphs` fixture).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
from urllib.parse import parse_qs

import pytest
import rdflib
from rdflib.query import ResultRow


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass

    def _dataset(self) -> rdflib.Dataset:
        return self.server.dataset  # type: ignore[attr-defined]

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)
        self.server.last_params = params  # type: ignore[attr-defined]

        if self.path.endswith("/update") or "update" in params:
            update_text = params["update"][0]
            self._dataset().update(update_text)
            self.send_response(200)
            self.end_headers()
            return

        query_text = params["query"][0]
        default_graphs = params.get("default-graph-uri", [])
        result = self._run_query(query_text, default_graphs)
        if result.type in ("CONSTRUCT", "DESCRIBE"):
            assert result.graph is not None
            body_out = result.graph.serialize(format="turtle").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/turtle")
            self.end_headers()
            self.wfile.write(body_out)
        else:
            bindings = []
            result_vars = result.vars or []
            for row in result:
                row = cast(ResultRow, row)
                binding = {}
                for var in result_vars:
                    value = row[var]
                    if value is None:
                        continue
                    binding[str(var)] = {
                        "type": "uri" if isinstance(value, rdflib.URIRef) else "literal",
                        "value": str(value),
                    }
                bindings.append(binding)
            payload = {
                "head": {"vars": [str(v) for v in (result.vars or [])]},
                "results": {"bindings": bindings},
            }
            body_out = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/sparql-results+json")
            self.end_headers()
            self.wfile.write(body_out)

    def _run_query(self, query_text: str, default_graphs):
        ds = self._dataset()
        if default_graphs:
            merged = rdflib.Graph()
            for g in default_graphs:
                merged += ds.get_context(rdflib.URIRef(g))
            return merged.query(query_text)
        return ds.query(query_text, DEBUG=False)


class _Server(HTTPServer):
    dataset: rdflib.Dataset
    last_params: dict


@pytest.fixture
def fuseki_server():
    """Yields (query_url, update_url, dataset) for a running fake dataset. The
    dataset starts empty -- populate it via `dataset.get_context(uri).parse(...)`
    or `dataset.get_context(uri).add(...)` before making requests."""
    dataset = rdflib.Dataset()
    server = _Server(("127.0.0.1", 0), _Handler)
    server.dataset = dataset
    server.last_params = {}
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        yield f"{base}/query", f"{base}/update", dataset, server
    finally:
        server.shutdown()
        thread.join(timeout=5)
