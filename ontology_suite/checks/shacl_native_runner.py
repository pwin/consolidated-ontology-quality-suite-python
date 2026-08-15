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
A larger, denser fixture built specifically to stress multi-instance/
multi-focus-node scenarios (``examples/checks_stress_test/``,
``tests/test_engine_parity_stress.py``) confirms the same full parity for
all 18 checks that have both a SHACL and SPARQL formulation, including two
cases (``STY-003``, many blank-node focus nodes at once; ``STY-001``/
``STY-002``, blank-node-typed anonymous class/property expressions) that
each found a real, since-fixed native-engine bug -- see that test module's
own docstring for the history; requires ``shacl>=0.1.9``, re-verified at
full parity at that version too (0.1.6-0.1.9 added SHACL-AF ``sh:rule``
support and a recursion-guard performance fix, neither of which touches
the ``sh:sparql``/SELECT constraint path this suite's own checks use --
see the ``inference`` parameter's docstring below for the one visible,
unused-by-this-suite surface change).

**Fixed history: the two engines used to disagree on severity, because
the shapes declared it in a place only one of them reads.** SHACL defines
``sh:severity`` as a property of a *shape*. Every shape in
``resources/shapes/*.ttl`` used to declare it one level down instead, on
the ``sh:SPARQLConstraint`` blank node inside its ``sh:sparql [ ... ]``
block. That is legal Turtle but not something a processor is obliged to
look at, and pyshacl does not: it fell back to the SHACL spec default and
reported *every* SHACL-sourced finding as ``Violation``. The native engine
did read the nested form, so identical findings came back at different
severities -- ``examples/ontology/domain.ttl``'s 18 findings as 17
Violation/1 Warning/0 Info under ``--engine shacl``, and 1 Violation/15
Warning/2 Info under ``--engine native``. Since every subcommand defaults
to ``--fail-on Violation``, the same ontology passed or failed purely on
which ``--engine`` was passed. Fixed by moving each declaration onto its
enclosing shape: both engines now report 1 Violation/15 Warning/2 Info,
matching ``registry.json``. (A ``sh:severity`` on a SHACL-core property
shape, e.g. ``LOG-001``'s ``sh:property [...]``, was always read correctly
by both -- a property shape *is* a shape -- and is left as it was.)
``tests/test_shape_severity.py`` guards the placement;
``tests/test_shacl_native_runner.py`` compares the resulting severities
against the registry under both engines, and its ``_row_key`` now includes
severity in the identity comparison, where it used to exclude it precisely
because of this divergence.

**Fixed history: blank-node focus nodes used to cross-join under a
``sh:sparql`` constraint.** Before ``shacl`` 0.1.4, a shape whose target
could include *blank-node* focus nodes (in this suite's own shapes, only
``STY-003``'s ``sh:targetSubjectsOf rdfs:label`` -- a blank node can carry
an ``rdfs:label`` same as a named resource) didn't scope ``$this`` per
focus node correctly when evaluating the ``sh:sparql`` SPARQLConstraint:
N blank-node focus nodes sharing the shape produced N² findings (every
focus node cross-joined against every other focus node's own value)
instead of the correct N. Reported upstream and fixed in ``shacl`` 0.1.4
(https://github.com/pwin/SHACL_Engine); confirmed fixed here at N=2/3/4/60
-- see ``tests/test_engine_parity_stress.py``, which still carries a
regression test for this (it would fail loudly again if a pre-0.1.4 wheel
ever got reinstalled -- see the ``uv sync`` warning below for exactly how
that could happen silently).

**Fixed history: blank-node focus nodes used to leak past
``isIRI($this)``.** Found immediately after installing ``shacl`` 0.1.4 to
verify the N² cross-product fix above -- turned out to be a regression
from (or a boundary case left uncovered by) that same fix, since both
involve the same per-focus-node ``$this``-scoping machinery.
``FILTER(isIRI($this))`` inside a ``sh:sparql`` SPARQLConstraint stopped
excluding blank-node focus nodes in ``shacl`` 0.1.4: ``STY-001``
(``sh:targetClass owl:Class``) and ``STY-002`` (``sh:targetClass
owl:ObjectProperty``/``owl:DatatypeProperty``) both rely on exactly this
filter to exclude blank-node-typed anonymous class/property expressions
(``owl:unionOf``/``owl:intersectionOf`` members, ``owl:inverseOf``'s
anonymous object, etc. -- extremely common in real ontologies) from a
check that's only meaningful for a real, named local name. Confirmed
against real data: gist 14.1.0 alone has ~97 such blank nodes, and
``STY-001`` reported every one of them as a false positive under
``--engine native``/``native+sparql`` under 0.1.4, where pyshacl
(independent, pure-Python) correctly found none. Reported upstream and
fixed in ``shacl`` 0.1.5 -- confirmed fixed here at the same scale (real
gist data, 56/56 full parity) -- see
``tests/test_engine_parity_stress.py::test_native_isiri_this_blank_node_regression``
(and its ``_sty002_isolated`` sibling) and
``tests/test_vehicle_gist_checks.py::test_native_engine_matches_pyshacl_on_the_real_vehicle_gist_fixture``,
which still carry regression tests for this (they would fail loudly again
if a pre-0.1.5 wheel ever got reinstalled).

``shacl`` is on PyPI as of 0.1.4 (0.1.5 for the fix above) -- install with
``uv sync --extra native-shacl`` (an opt-in extra, same convention as this
package's own ``reasoner`` extra; see ``pyproject.toml``, pinned
``shacl>=0.1.9``). Not a hard dependency of this package proper -- still
imported lazily here (same convention as ``shacl_runner.py``'s own
pyshacl import), so ``--engine shacl``/``sparql``
and everything else works fine without it. Requires ``shacl>=0.1.9``
(pinned in ``pyproject.toml``'s ``native-shacl`` extra, kept current with
upstream rather than pinned at the 0.1.5 floor that would technically
still work): 0.1.3/0.1.4 had the two blank-node focus-node bugs documented
above (N² cross-product, then ``isIRI($this)`` no longer excluding blank
nodes), and 0.1.3 specifically predates PyPI publication and this
package's ``Report.turtle``-based result-graph consumption
(``sh:ValidationReport``, not a hand-reconstructed flat list of
``Result`` objects -- see git history for the older, more manual approach
this replaced). 0.1.6-0.1.9 are pure feature/perf additions (SHACL-AF
rules, a recursion-guard speedup) with no behavior change to anything
this suite's own checks exercise -- re-verified at 0.1.9, not just
assumed from the changelog.

**Plain ``uv sync`` (no ``--extra native-shacl``) still uninstalls it** --
standard behavior for any opt-in extra, not special to this package, but
easy to trip over: running bare ``uv sync`` after having it installed
removes it again, with no warning beyond the routine ``- shacl==0.1.9``
line in ``uv sync``'s own output. ``--engine native``/``native+sparql``
then fail with a clear ``RuntimeError`` (see ``run_shacl_native`` below --
not silent corruption), but the CLI's own default engine
(``pipeline.default_engine()``) auto-selects ``native+sparql`` *whenever the
package is importable*, so losing it after a routine ``uv sync`` silently
changes which engine every subcommand runs by default -- a wall-clock and
cross-validation change now, rather than the severity change it also used
to be before the fix documented above. Always include the extra:
``uv sync --extra native-shacl`` (add ``--extra reasoner`` too if you also
want the optional DL-reasoner path).

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
    option) -- the subset this suite's own pipeline exposes; unlike
    pyshacl, there is no ``owlrl``/``both`` here (no OWL2-RL reasoner in
    the Rust engine) or ``ont_graph`` support. `pipeline.py` rejects those
    two values under ``--engine native``/``native+sparql`` before this is
    ever called; the native engine itself would also raise a
    ``ValueError`` for them. As of ``shacl`` 0.1.6 the engine itself also
    accepts ``"rules"``/``"rules-iterated"`` (SHACL-AF ``sh:rule``
    materialisation) -- not plumbed through here, since none of this
    suite's own ``resources/shapes/*.ttl`` declare a ``sh:rule`` for it to
    act on; nothing to lose by not exposing an inference mode with no
    shape in this suite that uses it.
    """
    if _native_shacl is None:
        raise RuntimeError(
            "the native `shacl` package is not installed -- run `uv sync --extra "
            "native-shacl` (see this module's docstring)"
        ) from _IMPORT_ERROR

    shapes = _native_shacl.Shapes.from_turtle(shapes_graph.serialize(format="turtle"))  # type: ignore[attr-defined]
    report = shapes.validate_turtle(data_graph.serialize(format="turtle"), inference=inference)

    results_graph = Graph()
    results_graph.parse(data=report.turtle, format="turtle")
    _resolve_blank_source_shapes(results_graph, _message_index(shapes_graph))

    text = f"native SHACL engine: conforms={report.conforms}, {len(report.results)} result(s)"
    return report.conforms, results_graph, text
