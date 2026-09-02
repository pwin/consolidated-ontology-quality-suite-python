"""
Combines the SHACL-native results graph and the standalone-SPARQL results
graph into one list of unified result rows, using ``registry.Registry`` to
resolve each result back to its check id, category, remediation text, etc.

Because several checks are implemented twice (once as a SHACL/SHACL-SPARQL
shape, once as a portable SPARQL CONSTRUCT query), the same real-world
violation can appear in both result graphs. This module deduplicates on
(check_id, focus_node, path, value) and records which engine(s) produced
each finding, which doubles as a regression check on the two formulations:
if a violation is only ever found by one engine, that is worth
investigating.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from rdflib import BNode, Graph
from rdflib.collection import Collection
from rdflib.namespace import RDF, Namespace

from .registry import Registry

SH = Namespace("http://www.w3.org/ns/shacl#")

# SHACL property-path operators, in the form `sh:<op>Path <inner>` -> the
# SPARQL 1.1 path-expression suffix that means the same thing.
_PATH_SUFFIXES = {
    SH.zeroOrMorePath: "*",
    SH.oneOrMorePath: "+",
    SH.zeroOrOnePath: "?",
}

SEVERITY_ORDER = {
    "http://www.w3.org/ns/shacl#Violation": 0,
    "http://www.w3.org/ns/shacl#Warning": 1,
    "http://www.w3.org/ns/shacl#Info": 2,
}
SEVERITY_LABEL = {
    "http://www.w3.org/ns/shacl#Violation": "Violation",
    "http://www.w3.org/ns/shacl#Warning": "Warning",
    "http://www.w3.org/ns/shacl#Info": "Info",
}


@dataclass
class ResultRow:
    check_id: Optional[str]
    category: Optional[str]
    title: Optional[str]
    severity: str
    focus_node: str
    path: Optional[str]
    value: Optional[str]
    message: str
    remediation: Optional[str]
    sources: List[str] = field(default_factory=list)


def _path_expression(graph: Graph, node, _depth: int = 0) -> str:
    """Render one ``sh:resultPath`` value as a stable, readable SPARQL 1.1
    property-path expression.

    A path is only sometimes a plain IRI. SHACL also allows *path
    expressions*, which are encoded as blank-node structures
    (``[ sh:oneOrMorePath rdfs:subClassOf ]``, ``[ sh:inversePath ... ]``,
    an RDF list for a sequence, ``sh:alternativePath`` for ``|``). Rendering
    those with a plain ``str()`` yields the blank node's *identifier* --
    which rdflib mints fresh on every parse, so the same finding got a
    different ``path`` string on every run. That made ``full_results.csv``
    diff against itself with no input change, and, because ``path`` is part
    of the dedup key in ``build_unified_results``, it also meant the pyshacl
    and SPARQL formulations of the same path-expression finding could never
    merge -- their blank node ids never match. LOG-001 (``sh:path [
    sh:oneOrMorePath rdfs:subClassOf ]``) is this suite's own instance of it.

    Falls back to ``str(node)`` for anything not recognized, so an
    unexpected shape is degraded output rather than an exception.
    """
    if not isinstance(node, BNode) or _depth > 10:
        return str(node)

    for operator, suffix in _PATH_SUFFIXES.items():
        inner = graph.value(node, operator)
        if inner is not None:
            return f"({_path_expression(graph, inner, _depth + 1)}){suffix}"

    inverse = graph.value(node, SH.inversePath)
    if inverse is not None:
        return f"^({_path_expression(graph, inverse, _depth + 1)})"

    alternative = graph.value(node, SH.alternativePath)
    if alternative is not None:
        members = list(Collection(graph, alternative))
        return "|".join(_path_expression(graph, m, _depth + 1) for m in members)

    if graph.value(node, RDF.first) is not None:  # sequence path (an RDF list)
        members = list(Collection(graph, node))
        return "/".join(_path_expression(graph, m, _depth + 1) for m in members)

    return str(node)


def _joined(values: List[str]) -> Optional[str]:
    """One display string for a result property that may legitimately carry
    several values, ordered so the same finding always renders identically.

    Several of this suite's SPARQL CONSTRUCTs bind two values for one
    finding on purpose -- ``LOG-004`` emits ``sh:value ?p1, ?p2`` (the two
    inverses it is complaining about), ``LOG-006``/``LOG-007`` emit both the
    domain and the range, ``REA-001`` both disjoint classes, ``STR-007``
    both subject and object. Reading a single one of them back with
    ``Graph.value()`` picks an arbitrary member of an unordered set: which
    one came back varied per run, so rows collapsed differently under the
    dedup key each time and finding *totals* fluctuated with no input change
    (observed directly: LOG-004 reported 3, then 2, then 4 times across
    three consecutive identical runs of the same fixture). Sorting and
    joining makes the key order-independent, and shows both values in the
    report instead of half the finding.
    """
    if not values:
        return None
    return ", ".join(sorted(set(values)))


def substitute_message_placeholders(
    message: Optional[str],
    focus: Optional[str],
    path: Optional[str],
    value: Optional[str],
) -> str:
    """Fill in a SHACL message's `{$this}` / `{$value}` / `{$path}`.

    `sh:message` is a template: SHACL 1.0 section 6.2 substitutes the
    constraint's own bindings into it, and the placeholder is invariably the
    part that says *which* term the finding is about. An engine that returns
    the text verbatim therefore produces a finding a reader cannot act on --
    "{$this} is disjoint with one of its own transitive superclasses" names
    no class at all.

    Both engines needed this, to different degrees, and neither said so.
    Measured over examples/ontology/domain.ttl + examples/property_axioms/
    (84 SHACL rows): pyshacl left 2 unsubstituted, the native Rust engine
    left **25**, across ten check ids. The native engine is the default when
    it is installed, so the worse of the two was what most runs got. The
    portable SPARQL twins build their messages with CONCAT and were never
    affected, which is why `--engine sparql` reads correctly and is also why
    this went unnoticed: the two formulations of one check disagreed about
    the prose while agreeing about everything the dedup key looks at.

    Only the three bindings a result actually carries are substituted. A
    constraint parameter such as `{$maxCount}` is left as written rather than
    replaced with "None": that value is genuinely not in the result, and a
    visible placeholder at least says which term is missing instead of
    asserting a wrong one. The same rule the VS Code extension settled on
    when it found this from the other side.

    Called at ResultRow construction, never earlier. `shacl_native_runner`
    indexes the shapes graph on `sh:message` text to resolve blank source
    shapes, so substituting before that ran would break check-id resolution.
    """
    if not message or "{" not in message:
        return message or ""
    for name, replacement in (("this", focus), ("value", value), ("path", path)):
        if replacement is None:
            continue
        message = message.replace("{$" + name + "}", str(replacement))
        message = message.replace("{?" + name + "}", str(replacement))
    return message


def _extract_rows(
    results_graph: Graph,
    registry: Registry,
    shapes_graph: Optional[Graph],
    source_label: str,
) -> List[ResultRow]:
    rows: List[ResultRow] = []
    for result in results_graph.subjects(SH.resultSeverity, None):
        severity = results_graph.value(result, SH.resultSeverity)
        focus = results_graph.value(result, SH.focusNode)
        path = _joined([
            _path_expression(results_graph, p)
            for p in results_graph.objects(result, SH.resultPath)
        ])
        value = _joined([str(v) for v in results_graph.objects(result, SH.value)])
        message = results_graph.value(result, SH.resultMessage)
        scc = results_graph.value(result, SH.sourceConstraintComponent)
        shape = results_graph.value(result, SH.sourceShape)

        check_id = registry.resolve_check_id(scc, shape, shapes_graph)
        check = registry.get(check_id) if check_id else None

        # A check whose SPARQL CONSTRUCT never binds sh:value (common --
        # 18 of ~50 checks in this suite don't) leaves it unset; pyshacl,
        # for the matching sh:sparql SHACL formulation, defaults sh:value
        # to $this (the focus node) per the SHACL spec whenever its
        # sh:select query doesn't select its own ?value column. Without
        # normalizing the same way here, those two formulations of the
        # *same* finding produce different dedup keys below (value=None vs
        # value=<focus node>) and both survive as separate rows -- caught
        # as a real bug: STR-003 and QUA-001 each showed up with exactly
        # double their real finding count against a vehicle ontology
        # importing gist 14.1.0, purely from this mismatch, not from any
        # actual difference in what the two engines found.
        if value is None and focus is not None:
            value = str(focus)

        rows.append(
            ResultRow(
                check_id=check_id,
                category=check.category if check else None,
                title=check.title if check else None,
                severity=SEVERITY_LABEL.get(str(severity), str(severity)),
                focus_node=str(focus) if focus is not None else "",
                path=path,
                value=value,
                message=substitute_message_placeholders(
                    str(message) if message is not None else "",
                    str(focus) if focus is not None else None,
                    path,
                    value,
                ),
                remediation=check.remediation if check else None,
                sources=[source_label],
            )
        )
    return rows


def build_unified_results(
    shacl_results_graph: Graph,
    sparql_results_graph: Graph,
    registry: Registry,
    shapes_graph: Optional[Graph] = None,
    extra_results: Optional[Sequence[Tuple[Graph, str]]] = None,
    shacl_rows: Optional[List[ResultRow]] = None,
) -> List[ResultRow]:
    """``extra_results`` takes ``(results_graph, source_label)`` pairs for
    findings produced by neither engine -- currently only
    ``checks/literal_typing.py``, which covers the part of ``DAT-001`` no
    SPARQL formulation can reach. They merge under the same dedup key as
    everything else, so a finding both a portable formulation and a
    supplement report stays one row with both labels in ``sources``, and is
    listed last so an engine-produced row keeps its own message."""
    # ``shacl_rows`` lets a caller hand over rows it already has, skipping the
    # results-graph round-trip entirely -- the native engine's structured
    # results take that path (see shacl_native_runner.run_shacl_native_rows,
    # and the measurement in its docstring for why). The graph route stays the
    # default so pyshacl, which only reports as RDF, is unaffected.
    if shacl_rows is None:
        shacl_rows = _extract_rows(shacl_results_graph, registry, shapes_graph, "shacl")
    sparql_rows = _extract_rows(sparql_results_graph, registry, None, "sparql")
    extra_rows = [
        row
        for graph, label in (extra_results or [])
        for row in _extract_rows(graph, registry, None, label)
    ]

    merged: Dict[tuple, ResultRow] = {}
    for row in shacl_rows + sparql_rows + extra_rows:
        key = (row.check_id, row.focus_node, row.path, row.value)
        if key in merged:
            merged[key].sources = sorted(set(merged[key].sources + row.sources))
        else:
            merged[key] = row

    # Stable ordering: severity, then category, then check id, then focus node
    def sort_key(r: ResultRow):
        return (
            SEVERITY_ORDER.get(f"http://www.w3.org/ns/shacl#{r.severity}", 9),
            r.category or "zzz",
            r.check_id or "zzz",
            r.focus_node,
        )

    return sorted(merged.values(), key=sort_key)
