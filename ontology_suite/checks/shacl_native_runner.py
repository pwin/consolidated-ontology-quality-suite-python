"""Runs the SHACL half of the suite using the native (Rust) SHACL engine's
Python bindings (``shacl``, https://github.com/pwin/SHACL_Engine) instead of
pyshacl -- typically dramatically faster for the same findings (that repo's
own benchmarks: 58-104x on synthetic data against plain constraint
components; this suite's own shapes lean heavily on SHACL-SPARQL, where the
gap narrows since both engines then spend their time inside a query
evaluator -- see docs/ARCHITECTURE.md's pyshacl-vs-portable-SPARQL note for
the equivalent caveat). Verified to find the exact same 18 findings pyshacl
does against ``examples/ontology/domain.ttl`` (see
``tests/test_shacl_native_runner.py``).

``shacl`` isn't published to PyPI yet: install a prebuilt wheel from a
SHACL_Engine GitHub Release (matching your platform), or build one with
``maturin build --release`` in that repo's ``crates/shacl-python``, then
``uv pip install <the wheel>``. Not a hard dependency of this package --
imported lazily here, same convention as ``shacl_runner.py``'s own pyshacl
import.

Blank-node sourceShape resolution
----------------------------------
For a finding whose constraint is a SHACL-SPARQL constraint
(``sh:sparql [...]``), both pyshacl and the native engine report
``sh:sourceShape`` as the *enclosing named shape* (e.g. ``oq:EFF-001``) --
matching SHACL-SPARQL's spec semantics, where the constraint is a property
of the shape that declares it. No blank node is involved; `registry.py`'s
existing ``resolve_check_id`` handles this unchanged.

For a finding whose constraint is native SHACL core, nested in an anonymous
PropertyShape (``sh:property [ sh:disjoint ... ]`` etc.), pyshacl reports
``sh:sourceShape`` as *that* blank node, and ``resolve_check_id`` walks from
it back up to the enclosing named/annotated shape -- which only works
because pyshacl's blank node *is* the very same node already present in the
``shapes_graph`` object being walked.

The native engine parses shapes independently (in Rust), so the blank node
identifiers it reports share no identity with the ones rdflib mints parsing
the *same* file here -- blank node identifiers are never stable across
independent parses (the same fact ``versioning/diff.py``'s ``_named()``
docstring already documents, for the same underlying reason). Re-resolving
by identity is therefore impossible; this module resolves by ``sh:message``
text instead: the native engine returns each result's message
*unsubstituted* (literally ``"{$this} is disjoint with..."``, not filled
in), which is exactly the literal already sitting on that same blank/named
node in ``shapes_graph`` -- so it's used as an alternate key back to *that
exact node object*, which ``registry.resolve_check_id`` can then walk from
correctly, unmodified.

This assumes each ``sh:message`` text in the shapes set is unique per check
id (true for this suite's own ``shapes/*.ttl`` at the time of writing -- the
one duplicate found belongs to two nodes of the *same* check,
``oq:STY-002-obj``/``oq:STY-002-data``, so it resolves to the same check id
either way and is harmless). A constraint with no ``sh:message`` at all
falls back to an unresolved check id -- the same degraded behavior the
SHACL-core path already had before ``registry.py``'s own checkId-annotation
fix, not a new limitation this module introduces.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from rdflib import BNode, Graph, Literal
from rdflib.namespace import Namespace
from rdflib.term import Node
from rdflib.util import from_n3

try:
    import shacl as _native_shacl  # type: ignore[import-not-found]  # not a PyPI package; see module docstring
except ImportError as exc:  # pragma: no cover
    _native_shacl = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

SH = Namespace("http://www.w3.org/ns/shacl#")
_SEVERITY_IRI = {"Violation": SH.Violation, "Warning": SH.Warning, "Info": SH.Info}


def available() -> bool:
    return _native_shacl is not None


def _parse_term(n3: str) -> Node:
    """`from_n3` is typed `Optional[Union[Node, str]]` because it also
    accepts a string `default` for unparseable input -- irrelevant here,
    since every string this module feeds it comes straight from the native
    engine's own N-Triples-syntax term serialization (see this module's
    docstring), never from that fallback path."""
    term = from_n3(n3)
    assert isinstance(term, Node)
    return term


def _message_index(shapes_graph: Graph) -> Dict[str, Node]:
    """``sh:message`` text -> the exact `shapes_graph` node (URIRef or BNode)
    that carries it -- see module docstring."""
    return {str(message): node for node, message in shapes_graph.subject_objects(SH.message)}


def _resolve_source_shape(result, message_index: Dict[str, Node]) -> Optional[Node]:
    if result.source_shape and not result.source_shape.startswith("_:"):
        return _parse_term(result.source_shape)  # already a real, directly-named shape IRI -- trust it
    return message_index.get(result.message)  # blank node reported; resolve via message text instead


def run_shacl_native(data_graph: Graph, shapes_graph: Graph) -> Tuple[bool, Graph, str]:
    """Runs the native engine and returns ``(conforms, results_graph,
    results_text)`` -- the same shape ``shacl_runner.run_shacl()`` (pyshacl)
    returns, so callers don't need to know which engine produced it.
    Unlike pyshacl, there is no ``ont_graph``/``inference`` support here --
    the native engine validates the graph exactly as given.
    """
    if _native_shacl is None:
        raise RuntimeError(
            "the native `shacl` package is not installed -- see this module's docstring "
            "for how to get it (not yet on PyPI)"
        ) from _IMPORT_ERROR

    shapes = _native_shacl.Shapes.from_turtle(shapes_graph.serialize(format="turtle"))  # type: ignore[attr-defined]
    report = shapes.validate_turtle(data_graph.serialize(format="turtle"))

    message_index = _message_index(shapes_graph)
    results_graph = Graph()
    for i, result in enumerate(report.results):
        node = BNode(f"native-result-{i}")
        results_graph.add((node, SH.resultSeverity, _SEVERITY_IRI.get(result.severity, SH.Info)))
        results_graph.add((node, SH.resultMessage, Literal(result.message)))
        results_graph.add((node, SH.sourceConstraintComponent, SH[result.component]))
        if result.focus_node:
            results_graph.add((node, SH.focusNode, _parse_term(result.focus_node)))
        if result.path:
            results_graph.add((node, SH.resultPath, _parse_term(result.path)))
        if result.value:
            results_graph.add((node, SH.value, _parse_term(result.value)))
        source_shape = _resolve_source_shape(result, message_index)
        if source_shape is not None:
            results_graph.add((node, SH.sourceShape, source_shape))

    text = f"native SHACL engine: conforms={report.conforms}, {len(report.results)} result(s)"
    return report.conforms, results_graph, text
