"""Runs the SHACL half of the suite using the native (Rust) SHACL engine's
Python bindings (``shacl``, https://github.com/pwin/SHACL_Engine) instead of
pyshacl -- typically dramatically faster for the same findings (that repo's
own benchmarks: 58-104x on synthetic data against plain constraint
components; this suite's own shapes lean heavily on SHACL-SPARQL, where the
gap narrows since both engines then spend their time inside a query
evaluator -- see docs/ARCHITECTURE.md's pyshacl-vs-portable-SPARQL note for
the equivalent caveat). Verified to find the exact same findings pyshacl
does by identity -- ``(check_id, focus_node, value)``, both on
``examples/ontology/domain.ttl`` (18/18) and the ~3,300-triple
vehicle-ontology fixture (38/38) -- see ``tests/test_shacl_native_runner.py``.

**Identity is proven; severity is not, and the two engines genuinely
disagree on it.** pyshacl silently drops an ``sh:severity`` declared
*inside* an ``sh:sparql [...]`` SPARQLConstraint block and always reports
``Violation`` regardless of what the shape actually says; the native engine
reports the declared severity correctly (per the SHACL-AF spec -- this is a
pyshacl limitation, not a native-engine bug). Confirmed directly on this
suite's own shapes: ``examples/ontology/domain.ttl`` via ``--engine shacl``
reports 17 Violation/1 Warning/0 Info for the same 18 findings
``--engine native`` reports as 1 Violation/15 Warning/2 Info --
``QUA-001``/``QUA-002``/``STY-001``/``STY-002``/``STY-003``/``STR-003``/
``EFF-001`` all declare ``sh:severity`` this way in ``resources/shapes/*.ttl``
and pyshacl reports every one of them as ``Violation`` regardless. (A
``sh:severity`` declared directly on a SHACL-core shape, e.g. ``LOG-001``'s
``sh:property [...]``, is unaffected -- pyshacl handles that form
correctly; it's specifically the SPARQLConstraint-nested form it drops.)
This matters in practice because every subcommand defaults to
``--fail-on Violation``: the same ontology can pass or fail purely
depending on which ``--engine`` flag is passed. ``tests/test_shacl_native_runner.py``'s
``_row_key`` deliberately excludes severity from the identity comparison
for exactly this reason -- it is not an oversight, it is documenting a
known, confirmed divergence.

``shacl`` isn't published to PyPI yet: install a prebuilt wheel from a
SHACL_Engine GitHub Release (matching your platform), or build one with
``maturin build --release`` in that repo's ``crates/shacl-python``, then
``uv pip install <the wheel>``. Not a hard dependency of this package --
imported lazily here, same convention as ``shacl_runner.py``'s own pyshacl
import. Requires ``shacl>=0.1.3``: earlier versions' bindings returned only
a flat list of `Result` objects, not the real ``sh:ValidationReport`` graph
this module now consumes directly (``Report.turtle``, added in that
version) -- see git history for the previous, more manual approach this
replaced.

**``uv sync`` will silently uninstall it.** Since ``shacl`` isn't a
``pyproject.toml`` dependency (it can't be, not being on PyPI), ``uv``
treats it as drift from ``uv.lock`` and removes it on every ``uv sync`` --
no warning beyond the routine ``- shacl==0.1.3 (from file:///...)`` line in
``uv sync``'s own output, easy to miss. ``--engine native``/``native+sparql``
then fail with a clear ``RuntimeError`` (see ``run_shacl_native`` below --
not silent corruption), but the CLI's own default engine
(``pipeline.default_engine()``) auto-selects ``native+sparql`` *whenever the
package is importable*, so losing it after a routine ``uv sync`` silently
changes which engine every subcommand runs by default, with the severity
consequences documented above. Reinstall after every ``uv sync``:
``uv pip install --python .venv/Scripts/python.exe <the wheel>`` -- pass
``--python`` explicitly rather than relying on the current directory, since
``uv pip install`` respects a stray ``VIRTUAL_ENV`` environment variable
over the current project's venv (an easy mistake in a multi-repo shell
session).

Blank-node sourceShape resolution
----------------------------------
``Report.turtle`` is the real SHACL report graph the spec defines (the
engine's own round-trip test -- serialise, reparse, compare -- makes this a
much better bet than reconstructing the same triples by hand from the
`Result` objects, which this module used to do). Parsing it straight into
`results_graph` therefore gets every field `checks/merge.py::_extract_rows`
looks for -- ``sh:resultSeverity``, ``sh:focusNode``, ``sh:resultPath``
(including a complex path expression's own supporting triples, e.g.
``sh:oneOrMorePath``, which the old hand-built version never emitted at
all), ``sh:value``, ``sh:resultMessage``, ``sh:sourceConstraintComponent``,
and ``sh:sourceShape`` -- for free, with no per-field reconstruction here.

The one thing parsing the report alone can't fix: for a finding whose
constraint is native SHACL core, nested in an anonymous PropertyShape
(``sh:property [ sh:disjoint ... ]`` etc.), the engine reports
``sh:sourceShape`` as *that* blank node -- correct SHACL, but a blank node
minted by the Rust engine's own independent parse of the shapes graph, which
shares no identity with the ones rdflib mints parsing the *same* file here
(blank node identifiers are never stable across independent parses -- the
same fact ``versioning/diff.py``'s ``_named()`` docstring already documents,
for the same underlying reason). `registry.py`'s ``resolve_check_id`` needs
that node to actually be present in ``shapes_graph`` to walk from it, so
this module re-resolves it by ``sh:message`` text instead: the engine
returns each result's message *unsubstituted* (literally ``"{$this} is
disjoint with..."``, not filled in), which is exactly the literal already
sitting on that same blank/named node in ``shapes_graph`` -- used as an
alternate key back to *that exact node object*.

(For a SHACL-SPARQL constraint -- ``sh:sparql [...]`` -- both pyshacl and
the native engine report ``sh:sourceShape`` as the *enclosing named shape*
already, e.g. ``oq:EFF-001``, matching SHACL-SPARQL's spec semantics; no
blank node, no substitution needed.)

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

from typing import Dict, Tuple

from rdflib import BNode, Graph
from rdflib.namespace import Namespace
from rdflib.term import Node

try:
    import shacl as _native_shacl  # type: ignore[import-not-found]  # not a PyPI package; see module docstring
except ImportError as exc:  # pragma: no cover
    _native_shacl = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

SH = Namespace("http://www.w3.org/ns/shacl#")


def available() -> bool:
    return _native_shacl is not None


def _message_index(shapes_graph: Graph) -> Dict[str, Node]:
    """``sh:message`` text -> the exact `shapes_graph` node (URIRef or BNode)
    that carries it -- see module docstring."""
    return {str(message): node for node, message in shapes_graph.subject_objects(SH.message)}


def _resolve_blank_source_shapes(results_graph: Graph, message_index: Dict[str, Node]) -> None:
    """Rewrites each result's `sh:sourceShape`, in place, from an
    engine-local blank node to the corresponding real node in
    `shapes_graph` (found via `sh:resultMessage`) -- see module docstring."""
    for result_node in list(results_graph.subjects(SH.resultSeverity, None)):
        source_shape = results_graph.value(result_node, SH.sourceShape)
        if not isinstance(source_shape, BNode):
            continue
        message = results_graph.value(result_node, SH.resultMessage)
        resolved = message_index.get(str(message)) if message is not None else None
        if resolved is not None:
            results_graph.remove((result_node, SH.sourceShape, source_shape))
            results_graph.add((result_node, SH.sourceShape, resolved))


def run_shacl_native(data_graph: Graph, shapes_graph: Graph, *, inference: str = "none") -> Tuple[bool, Graph, str]:
    """Runs the native engine and returns ``(conforms, results_graph,
    results_text)`` -- the same shape ``shacl_runner.run_shacl()`` (pyshacl)
    returns, so callers don't need to know which engine produced it.

    ``inference`` supports ``"none"`` or ``"rdfs"`` (materialised into the
    data graph before validation, same semantics as pyshacl's own RDFS
    option) -- the native engine's own supported subset; unlike pyshacl,
    there is no ``owlrl``/``both`` here (no OWL2-RL reasoner in the Rust
    engine) or ``ont_graph`` support. `pipeline.py` rejects those two values
    under ``--engine native``/``native+sparql`` before this is ever called;
    the native engine itself would also raise a ``ValueError`` for them.
    """
    if _native_shacl is None:
        raise RuntimeError(
            "the native `shacl` package is not installed -- see this module's docstring "
            "for how to get it (not yet on PyPI)"
        ) from _IMPORT_ERROR

    shapes = _native_shacl.Shapes.from_turtle(shapes_graph.serialize(format="turtle"))  # type: ignore[attr-defined]
    report = shapes.validate_turtle(data_graph.serialize(format="turtle"), inference=inference)

    results_graph = Graph()
    results_graph.parse(data=report.turtle, format="turtle")
    _resolve_blank_source_shapes(results_graph, _message_index(shapes_graph))

    text = f"native SHACL engine: conforms={report.conforms}, {len(report.results)} result(s)"
    return report.conforms, results_graph, text
