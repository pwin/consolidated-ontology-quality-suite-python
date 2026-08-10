"""Tests for io_utils: URL-safe path handling and gzip transparency, for
both local files and (via a real in-process HTTP server, not a mock) URLs.
"""
import gzip
import http.server
import threading

import pytest
import rdflib

from ontology_suite import io_utils

TTL = b"@prefix ex: <https://example.org/> .\nex:a a ex:Thing .\n"


@pytest.fixture
def http_server(tmp_path):
    """Serves files from tmp_path over plain HTTP, on an OS-assigned port."""
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(tmp_path), **kwargs
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


# --- is_url / guess_format ---------------------------------------------------

def test_is_url():
    assert io_utils.is_url("https://example.org/foo.ttl")
    assert io_utils.is_url("http://example.org/foo.ttl")
    assert not io_utils.is_url("C:/repos/foo.ttl")
    assert not io_utils.is_url("/tmp/foo.ttl")
    assert not io_utils.is_url("foo.ttl")


def test_guess_format():
    assert io_utils.guess_format("foo.ttl") == "turtle"
    assert io_utils.guess_format("foo.owl") == "xml"
    assert io_utils.guess_format("foo.nt") == "nt"
    assert io_utils.guess_format("https://example.org/foo.ttl") == "turtle"
    assert io_utils.guess_format("foo.ttl.gz") == "turtle"
    assert io_utils.guess_format("https://example.org/foo.rdf.gz") == "xml"
    assert io_utils.guess_format("foo.unknown") == "turtle"


# --- local files, plain and gzip ---------------------------------------------

def test_read_bytes_local_plain(tmp_path):
    p = tmp_path / "foo.ttl"
    p.write_bytes(TTL)
    assert io_utils.read_bytes(p) == TTL


def test_read_bytes_local_gzip(tmp_path):
    p = tmp_path / "foo.ttl.gz"
    p.write_bytes(gzip.compress(TTL))
    assert io_utils.read_bytes(p) == TTL


def test_parse_graph_local_gzip(tmp_path):
    p = tmp_path / "foo.ttl.gz"
    p.write_bytes(gzip.compress(TTL))
    g = io_utils.parse_graph(rdflib.Graph(), p)
    assert len(g) == 1


# --- http(s) URLs, plain and gzip --------------------------------------------

def test_read_bytes_url_plain(http_server, tmp_path):
    (tmp_path / "foo.ttl").write_bytes(TTL)
    assert io_utils.read_bytes(f"{http_server}/foo.ttl") == TTL


def test_read_bytes_url_gzip(http_server, tmp_path):
    """A .gz-suffixed URL, and also the case of gzip content sniffed from
    magic bytes regardless of the URL's own extension (e.g. server-side
    Content-Encoding without a .gz-suffixed path)."""
    (tmp_path / "foo.ttl.gz").write_bytes(gzip.compress(TTL))
    assert io_utils.read_bytes(f"{http_server}/foo.ttl.gz") == TTL

    (tmp_path / "foo.ttl").write_bytes(gzip.compress(TTL))  # gzip bytes under a plain .ttl name
    assert io_utils.read_bytes(f"{http_server}/foo.ttl") == TTL


def test_parse_graph_url(http_server, tmp_path):
    (tmp_path / "foo.ttl").write_bytes(TTL)
    g = io_utils.parse_graph(rdflib.Graph(), f"{http_server}/foo.ttl")
    assert len(g) == 1


def test_read_bytes_url_blocked_without_allow_network(http_server, tmp_path):
    (tmp_path / "foo.ttl").write_bytes(TTL)
    with pytest.raises(PermissionError):
        io_utils.read_bytes(f"{http_server}/foo.ttl", allow_network=False)


def test_local_file_unaffected_by_allow_network_false(tmp_path):
    """allow_network only gates URL fetches -- a local path is always readable."""
    p = tmp_path / "foo.ttl"
    p.write_bytes(TTL)
    assert io_utils.read_bytes(p, allow_network=False) == TTL


def test_read_bytes_url_404(http_server):
    with pytest.raises(OSError):
        io_utils.read_bytes(f"{http_server}/does-not-exist.ttl")


# --- expand_sources -----------------------------------------------------------

def test_expand_sources_mixes_files_folders_and_urls(tmp_path, http_server):
    (tmp_path / "a.ttl").write_text("", encoding="utf-8")
    (tmp_path / "b.ttl").write_text("", encoding="utf-8")
    (tmp_path / "c.rq").write_text("", encoding="utf-8")
    single = tmp_path / "single.ttl"
    single.write_text("", encoding="utf-8")

    result = io_utils.expand_sources([tmp_path, single, f"{http_server}/remote.ttl"], "*.ttl")
    assert result == [
        str(tmp_path / "a.ttl"), str(tmp_path / "b.ttl"), str(single), f"{http_server}/remote.ttl",
    ]


def test_expand_sources_discovers_gz_variants_in_a_folder(tmp_path):
    (tmp_path / "a.ttl").write_text("", encoding="utf-8")
    (tmp_path / "b.ttl.gz").write_bytes(gzip.compress(TTL))

    result = io_utils.expand_sources([tmp_path], "*.ttl")
    assert set(result) == {str(tmp_path / "a.ttl"), str(tmp_path / "b.ttl.gz")}


def test_expand_sources_deduplicates_and_preserves_order(tmp_path):
    p = tmp_path / "a.ttl"
    p.write_text("", encoding="utf-8")
    result = io_utils.expand_sources([p, p, tmp_path], "*.ttl")
    assert result == [str(p)]
