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

Two more edge cases fixed after comparing this module against the sibling
``New_SHACL_Engine`` project's own (Rust, `ureq`-based) URL loader, which
handles both more carefully:

- **Query strings and fragments were not stripped before guessing a URL's
  format** -- ``guess_format("https://ex.org/d.owl?v=2")`` returned
  ``"turtle"`` (silently wrong) instead of ``"xml"``, since
  ``os.path.splitext`` saw the extension as ``.owl?v=2``, not ``.owl``.
- **Fetches were unbounded.** `urllib`'s reader has no size limit of its
  own -- a malicious or merely huge URL could exhaust memory before
  `read_bytes` ever returns. `FETCH_LIMIT` below matches the Rust project's
  own reasoned constant (1 GiB: comfortably past any real ontology/data
  file, and refusing early is kinder than an out-of-memory kill partway
  through); checked against a `Content-Length` header first where present,
  and against the actual bytes read either way, so a response that lies
  about -- or omits -- its own length is still caught.
"""
from __future__ import annotations

import gzip
import io
import os
from codecs import BOM_UTF8
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
FETCH_LIMIT = 1 << 30  # 1 GiB

# Which formats are interchangeable enough that a content sniff should NOT
# override the extension's more specific guess. N-Triples and N3 are both
# read by the Turtle family, so a `.n3` file whose first directive is
# `@prefix` must stay `n3` -- N3 is a superset, and downgrading it to
# `turtle` would reject the very syntax the extension promised. Only a
# sniff from a *different* family overrides, which is the case that
# actually matters: Turtle content in a `.owl`/`.rdf` file, or RDF/XML
# content in a `.ttl` one.
_FORMAT_FAMILIES = {
    "turtle": "turtle", "n3": "turtle", "nt": "turtle",
    "xml": "xml", "json-ld": "json-ld",
}


def is_url(source: str | Path) -> bool:
    return urlparse(str(source)).scheme in ("http", "https")


def guess_format(source: str | Path) -> str:
    """RDF serialization format from `source`'s file extension -- a plain
    string operation (`os.path.splitext`, not `Path.suffix`) so it works
    identically on a URL or a local path. A URL's query string/fragment is
    stripped first (`d.owl?v=2` and `d.owl#frag` both guess `xml`, not the
    `turtle` default `.owl?v=2` would otherwise fall through to), then a
    trailing `.gz`, so `foo.ttl.gz` still guesses `turtle`, not `foo.ttl.gz`'s
    own (unrecognized) extension."""
    path = str(source)
    if is_url(path):
        path = urlparse(path).path
    if path.lower().endswith(".gz"):
        path = path[:-3]
    ext = os.path.splitext(path)[1].lower()
    return _EXTENSION_FORMATS.get(ext, "turtle")


def read_bytes(source: str | Path, *, allow_network: bool = True, limit: int = FETCH_LIMIT) -> bytes:
    """Reads `source` (a local path or an http(s) URL) fully into memory,
    transparently gzip-decompressing if the content is gzip (sniffed from
    its magic bytes, regardless of a `.gz` suffix -- see module docstring).
    A URL response over `limit` bytes is refused -- checked against
    `Content-Length` first where the server sends one, and against the
    actual bytes read either way (see module docstring).
    """
    if is_url(source):
        if not allow_network:
            raise PermissionError(
                f"refusing to fetch {source!r} over the network here (pass allow_network=True / --allow-network to permit it)"
            )
        try:
            with urllib.request.urlopen(str(source)) as response:  # noqa: S310 - user-provided http(s) URL, by design
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > limit:
                    raise OSError(f"{source!r} declares {declared} bytes, over the {limit}-byte limit")
                raw = response.read(limit + 1)
        except urllib.error.URLError as exc:
            raise OSError(f"could not fetch {source!r}: {exc}") from exc
        if len(raw) > limit:
            raise OSError(f"{source!r} is over the {limit}-byte fetch limit")
    else:
        with open(source, "rb") as f:
            raw = f.read()

    if raw[:2] == _GZIP_MAGIC:
        raw = gzip.decompress(raw)
    return raw


def read_text(source: str | Path, *, allow_network: bool = True, encoding: str = "utf-8") -> str:
    return read_bytes(source, allow_network=allow_network).decode(encoding)


def sniff_format(raw: bytes) -> str | None:
    """The RDF serialization `raw` actually *is*, judged from its opening
    bytes, or None when nothing conclusive shows. Deliberately conservative:
    it recognizes only markers that cannot plausibly belong to another
    serialization, and returns None -- leaving `guess_format`'s extension
    guess in place -- for anything ambiguous. N-Triples, for instance, has
    no distinguishing header at all.

    This exists because the extension lies more often than you would think,
    and when it does the failure is silent and expensive. Caught from real
    user-reported output: an ontology whose `owl:imports` targets sat in
    `--import-dir` as `.owl` files written in Turtle (what Protege emits by
    default). `guess_format` mapped `.owl` -> `xml`, the RDF/XML parser
    threw on the first `@prefix`, and `resolve_imports` swallowed that in a
    bare `except Exception: continue` -- so every import came back
    unresolved, and every term declared in one was reported undeclared
    (`CNF-001`/`CNF-002`) against queries that were in fact correct. The
    reverse case, RDF/XML content in a `.ttl` file, fails the same way.
    """
    head = raw[:8192]
    if head[:3] == BOM_UTF8:
        head = head[3:]
    if head.lstrip()[:5].lower() == b"<?xml" or b"<rdf:RDF" in head:
        return "xml"
    for line in head.splitlines():
        line = line.strip()
        # Hand-written Turtle usually opens with a comment block; JSON-LD
        # never does, so skipping comments costs nothing and gains the
        # common case.
        if not line or line.startswith(b"#"):
            continue
        if line[:1] in (b"{", b"["):
            return "json-ld"
        lowered = line.lower()
        if any(lowered.startswith(d) for d in (b"@prefix", b"@base", b"prefix ", b"base ")):
            return "turtle"
        return None
    return None


def resolve_format(source: str | Path, raw: bytes) -> str:
    """The format to parse `raw` from `source` as: `guess_format`'s
    extension guess, unless `sniff_format` reads the content as a different
    *family* entirely (see `_FORMAT_FAMILIES`), in which case the content
    wins -- bytes are evidence, an extension is only a claim."""
    guessed = guess_format(source)
    sniffed = sniff_format(raw)
    if sniffed and _FORMAT_FAMILIES.get(sniffed) != _FORMAT_FAMILIES.get(guessed):
        return sniffed
    return guessed


def parse_graph(
    graph: Graph, source: str | Path, *, format: str | None = None, allow_network: bool = True
) -> Graph:
    """Parses `source` (local path, gzip-compressed local path, http(s)
    URL, or gzip-compressed http(s) URL) into `graph` in place. The format
    comes from `format` when given, else from `resolve_format` -- the
    extension's guess, overridden when the content plainly disagrees.
    Returns `graph`, for chaining.

    A parse failure is re-raised as a `ValueError` naming the format tried,
    the extension's guess and the content sniff, so a caller that reports
    the failure (`ontology_evaluation.resolve_imports`) can say *why* a file
    was skipped rather than only that it was."""
    raw = read_bytes(source, allow_network=allow_network)
    chosen = format or resolve_format(source, raw)
    try:
        graph.parse(data=raw, format=chosen)
    except Exception as exc:
        raise ValueError(
            f"could not parse {source} as {chosen!r} (extension suggests "
            f"{guess_format(source)!r}, content sniffs as {sniff_format(raw)!r}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
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
