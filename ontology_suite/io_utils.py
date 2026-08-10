"""Shared helpers for reading a local file, a gzip-compressed local file, an
http(s) URL, or a gzip-compressed http(s) URL -- used by every loader in
this suite so URL fetching, gzip transparency, and RDF-format guessing
behave identically everywhere, rather than each loader reimplementing (or
forgetting to reimplement) its own slightly different subset.

Two real gaps this module exists to close, found by testing actual
behavior rather than assuming it:

1. **URL mangling via `pathlib.Path`.** `Path("https://example.org/foo.ttl")`
   collapses the `//` into a single `/` (`WindowsPath('https:/example.org/foo.ttl')`
   on Windows -- confirmed by testing), silently turning a valid URL into a
   nonexistent local path. Every loader in this module works with plain
   strings throughout, never wrapping a source in `Path` before checking
   whether it's a URL.
2. **No gzip transparency anywhere.** `rdflib.Graph.parse()` does not sniff
   for gzip; handing it gzip-compressed bytes raises `UnicodeDecodeError`
   (confirmed by testing: it tries to decode the gzip magic bytes as
   UTF-8 text). Every read in this module checks for the gzip magic bytes
   (`\\x1f\\x8b`) and transparently decompresses -- this also covers a
   server-side `Content-Encoding: gzip` response with a plain `.ttl` URL,
   not just a `.gz`-suffixed source, since it's a content sniff, not an
   extension check.

``allow_network`` semantics (deliberately asymmetric, not a bug): a source
the caller names *explicitly* (a CLI ``--ontology``/``--data`` argument, an
entry in ``tarql_sources``/``ontology_paths``) is something the user already
consented to by typing it, so it defaults to allowed. A source *discovered*
while parsing other content -- the only case in this suite is an
``owl:imports`` target found inside an ontology file -- is not something the
user directly asked for, so `ontology_evaluation.resolve_imports` passes
``allow_network=False`` unless ``--allow-network`` was explicitly given.
Both paths go through this module's `allow_network` parameter; only the
caller's choice of what to pass differs.
"""
from __future__ import annotations

import gzip
import io
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urlparse

from rdflib import Graph

_EXTENSION_FORMATS = {
    ".ttl": "turtle", ".turtle": "turtle", ".n3": "n3",
    ".nt": "nt", ".rdf": "xml", ".owl": "xml", ".xml": "xml",
    ".jsonld": "json-ld",
}
_GZIP_MAGIC = b"\x1f\x8b"


def is_url(source: str | Path) -> bool:
    return urlparse(str(source)).scheme in ("http", "https")


def guess_format(source: str | Path) -> str:
    """RDF serialization format from `source`'s file extension -- a plain
    string operation (`os.path.splitext`, not `Path.suffix`) so it works
    identically on a URL or a local path. A trailing `.gz` is stripped
    first, so `foo.ttl.gz` still guesses `turtle`, not `foo.ttl.gz`'s own
    (unrecognized) extension."""
    path = str(source)
    if path.lower().endswith(".gz"):
        path = path[:-3]
    ext = os.path.splitext(path)[1].lower()
    return _EXTENSION_FORMATS.get(ext, "turtle")


def read_bytes(source: str | Path, *, allow_network: bool = True) -> bytes:
    """Reads `source` (a local path or an http(s) URL) fully into memory,
    transparently gzip-decompressing if the content is gzip (sniffed from
    its magic bytes, regardless of a `.gz` suffix -- see module docstring).
    """
    if is_url(source):
        if not allow_network:
            raise PermissionError(
                f"refusing to fetch {source!r} over the network here (pass allow_network=True / --allow-network to permit it)"
            )
        try:
            with urllib.request.urlopen(str(source)) as response:  # noqa: S310 - user-provided http(s) URL, by design
                raw = response.read()
        except urllib.error.URLError as exc:
            raise OSError(f"could not fetch {source!r}: {exc}") from exc
    else:
        with open(source, "rb") as f:
            raw = f.read()

    if raw[:2] == _GZIP_MAGIC:
        raw = gzip.decompress(raw)
    return raw


def read_text(source: str | Path, *, allow_network: bool = True, encoding: str = "utf-8") -> str:
    return read_bytes(source, allow_network=allow_network).decode(encoding)


def parse_graph(
    graph: Graph, source: str | Path, *, format: str | None = None, allow_network: bool = True
) -> Graph:
    """Parses `source` (local path, gzip-compressed local path, http(s)
    URL, or gzip-compressed http(s) URL) into `graph` in place, guessing
    the RDF format from `source`'s extension unless `format` is given.
    Returns `graph`, for chaining."""
    raw = read_bytes(source, allow_network=allow_network)
    graph.parse(data=raw, format=format or guess_format(source))
    return graph


def expand_sources(sources: Iterable[str | Path], patterns: str) -> List[str]:
    """Flattens a mix of file paths, folders, and http(s) URLs into an
    order-preserving, deduplicated list of source strings -- a URL-safe
    drop-in for the files-or-folders expansion pattern used throughout
    `sketch/prefix_alignment.py` and `sketch/tarql_visualiser.py`. A URL is
    never passed to `Path()` (see module docstring) and is never treated as
    a folder to glob -- only a local directory is. `patterns` is a
    comma-separated glob pattern list, e.g. `"*.ttl,*.rq"`; each pattern is
    also tried with a `.gz` suffix appended, so a folder of
    gzip-compressed sources is discovered too.
    """
    seen = set()
    ordered: List[str] = []
    pattern_list = [p.strip() for p in patterns.split(",")]
    pattern_list += [p + ".gz" for p in pattern_list]

    for src in sources:
        if is_url(src):
            candidates = [str(src)]
        else:
            path = Path(src)
            candidates = (
                sorted(str(p) for pattern in pattern_list for p in path.glob(pattern))
                if path.is_dir() else [str(path)]
            )
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
    return ordered
