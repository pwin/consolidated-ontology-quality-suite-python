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

from .registry import OQ, Registry

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
    # Where the finding is in a file, when that is knowable. Two routes, and
    # they differ in how much they can be trusted. The TARQL checks fill these
    # from `oq:sourceFile`/`oq:sourceLine`, which `bind_analysis` produced by
    # parsing the query text -- exact. Ontology findings get them from
    # `locate.locate_rows`, which searches the file for the focus node's
    # declaration -- best-effort, and `None` whenever it cannot settle the
    # question. Both stay `None` rather than guessing; see `checks/locate.py`.
    source_file: Optional[str] = None
    line: Optional[int] = None
    # Whether ``focus_node`` is a blank node label rather than an IRI. Set
    # from the RDF term at extraction, never guessed from the string: the two
    # routes into this dataclass render a blank node differently (the graph
    # route gives rdflib's bare label, the native engine's structured API
    # gives ``_:label``), and neither is distinguishable from a relative IRI
    # by inspection. ``build_unified_results`` needs it because a blank node's
    # label is not comparable between the engines -- see there.
    focus_is_blank: bool = False


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


def _without_blank_label(message: str, label: str) -> str:
    """`message` with an already-interpolated blank node label taken back out.

    The label is the engine's internal identifier for an anonymous node. It is
    not an address a reader can follow, it is not the same string on the next
    run, and it is not the same string in the other engine -- so a message
    built around it tells a reader nothing and makes two reports of identical
    findings diff against each other.

    Both spellings, because the two routes differ and they differ in *what
    they hand this function*: the graph route reads rdflib's bare label off a
    `BNode` (`n` plus 32 hex digits), while the native engine's structured API
    gives it already prefixed (`_:0_b36`). So the prefix is normalised off
    before anything is matched -- prefixing an already-prefixed label produced
    `_:_:0_b36`, which matched nothing, and the engine's own short labels then
    fell under the length floor below and survived into the prose. Measured on
    `examples/checks_stress_test`: 60 messages reading "A label on _:0_b36 has
    no language tag."

    The two spellings are then treated differently, because the risk is not
    symmetric. `_:` cannot begin an English word, so the prefixed form is
    unambiguous and is replaced whatever its length. A bare label could
    collide with prose, so that form is only hunted for when it is long enough
    to be an opaque token rather than a word.
    """
    bare = label[2:] if label.startswith("_:") else label
    if not bare:
        return message
    message = message.replace("_:" + bare, ANONYMOUS_FOCUS)
    if len(bare) >= 8:
        message = message.replace(bare, ANONYMOUS_FOCUS)
    return message


#: What a message calls a focus node that has no name. The portable `.rq`
#: checks build the same words with `IF(isBlank(?e), "[a blank node]", ...)`,
#: and the two arms of one check have to agree or `merge` shows a reader two
#: different sentences about one finding.
ANONYMOUS_FOCUS = "[a blank node]"


def substitute_message_placeholders(
    message: Optional[str],
    focus: Optional[str],
    path: Optional[str],
    value: Optional[str],
    focus_is_blank: bool = False,
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

    `focus_is_blank` is the other half of a defect whose first half was fixed
    in the queries. A blank node's label is an internal identifier: it names
    nothing a reader can look up, and it differs between runs and between
    engines. The portable checks stopped interpolating it by guarding `STR()`
    with `isBlank()`; a *shape* cannot, because `{$this}` is SHACL's own
    substitution and the engine has already made the text. So it is caught
    here instead, which fixes it for every shape at once rather than one
    `sh:message` at a time -- measured on `examples/checks_stress_test`, 60 of
    65 `STY-003` findings. Worth knowing why it survived the first fix:
    `--engine sparql` reads the query's message and was correct, while the
    default mode merges both formulations and shows the *shape's*, so the
    configuration most runs use was the one still naming an identifier.
    """
    if not message:
        return ""
    if focus_is_blank and focus:
        # Not just the placeholder route. pyshacl fills `{$this}` in itself
        # before the results graph reaches us -- the docstring above measures
        # it leaving only 2 of 84 unsubstituted -- so by the time a message
        # arrives here the label is usually *already* in the prose and there
        # is no placeholder left to catch. Both routes have to be covered or
        # the fix works only for the engine that was lazier about it.
        message = _without_blank_label(message, str(focus))
        # `sh:value` defaults to the focus node when a constraint binds no
        # value of its own (see `_extract_rows`), so when the focus is
        # anonymous the value placeholder is holding the same label and has to
        # be replaced with the same words.
        if value is not None and str(value) == str(focus):
            value = ANONYMOUS_FOCUS
        focus = ANONYMOUS_FOCUS
    if "{" not in message:
        return message
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

        # A check that knows where in a file its finding is says so with
        # `oq:sourceFile`/`oq:sourceLine` on the result. Only the TARQL checks
        # can: `bind_analysis` parses the query text, so it has the real line,
        # and the alternative was what TQL-004 and TQL-005 used to do --
        # CONCAT the position into the message prose, where nothing can sort,
        # filter or click it, and where it is then repeated by any renderer
        # that also shows the position properly.
        source_file = results_graph.value(result, OQ.sourceFile)
        source_line = results_graph.value(result, OQ.sourceLine)

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
                    focus_is_blank=isinstance(focus, BNode),
                ),
                remediation=check.remediation if check else None,
                sources=[source_label],
                source_file=str(source_file) if source_file is not None else None,
                line=int(source_line) if source_line is not None else None,
                focus_is_blank=isinstance(focus, BNode),
            )
        )
    return rows


def _anonymous_key(row: ResultRow, seen: Dict[tuple, int]) -> tuple:
    """A dedup key for a finding whose focus node is anonymous.

    A blank node's label is not usable in the key. The two formulations of one
    check see the *same* anonymous restriction under different labels, because
    the portable checks run on holosdb and the shapes run on the rdflib graph,
    and an engine is free to relabel blank nodes when it parses a document --
    holosdb and oxigraph both do (`checks/holos_sparql.py` has the measurement).
    Keyed on the label, every such finding therefore survived once per
    formulation: on `examples/checks_stress_test` that was 60 duplicated
    `STY-003` rows, 322 findings becoming 382.

    Dropping the focus node from the key instead would merge *distinct*
    anonymous findings that agree on check, path and value -- two unlabelled
    restrictions carrying the same untagged label text would collapse into one
    row, and the reader would fix one and believe they were done. So the key
    keeps a count rather than an identity: the nth anonymous finding one arm
    reports for a (check, path, value) pairs with the nth the other arm
    reports. Counts come out exactly right, duplicates collapse, and nothing
    within a single arm ever merges.

    Which specific pair is matched up is arbitrary, and cannot be otherwise --
    the labels carry no information to match on. It costs nothing: rows paired
    this way agree on every field a reader can act on, and differ only in a
    label that was already arbitrary and already varied between runs.
    """
    arm = row.sources[0] if row.sources else ""
    bucket = (arm, row.check_id, row.path, row.value)
    ordinal = seen.get(bucket, 0)
    seen[bucket] = ordinal + 1
    # No focus node in the key, and the ordinal in its place. The literal
    # marker keeps an anonymous finding from ever colliding with a named one
    # that happens to share the rest of the key.
    return (row.check_id, "<anonymous>", row.path, row.value, ordinal)


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
    # How many anonymous findings each arm has already contributed for a given
    # (check, path, value). See `_anonymous_ordinal` for what this buys.
    anonymous_seen: Dict[tuple, int] = {}
    for row in shacl_rows + sparql_rows + extra_rows:
        if row.focus_is_blank:
            key = _anonymous_key(row, anonymous_seen)
        else:
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
