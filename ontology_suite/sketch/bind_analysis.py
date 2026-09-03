"""Cross-file review of the ``BIND`` statements in a folder of TARQL/oxi-gen
CONSTRUCT queries.

These are *native* checks in the sense of ``docs/EXTENDING.md``: there is no
graph to pattern-match. ``tarql_visualiser.parse_query`` deliberately keeps
only a query's prefixes and its ``CONSTRUCT`` template, turning each variable
into a placeholder IRI; the ``WHERE`` clause, and with it every ``BIND``
expression and the identity of every variable, is discarded before the sketch
graph exists. So no SPARQL or SHACL formulation over the *sketch* can see any
of what this module looks at. It reads the query text instead.

That is a reason to write these three in Python, and it was mistaken for a
reason no TARQL check could ever be a query. ``bind_report_to_graph`` below
publishes what this module parses -- a node per ``BIND`` carrying its target,
expression, skeleton, file and line -- so a check over the query source is an
ordinary ``.rq`` in ``resources/sparql/tarql/``, added with a registry entry
and no code, exactly like ``STR-002``. Skeletonisation stays here because it
is a parse and SPARQL cannot do it; asking whether two files disagree about a
skeleton is then a ``GROUP BY``. Python parses, SPARQL asks.

What it is for
--------------
A folder of TARQL queries is a program, and like any program it drifts. The
same conceptual IRI gets minted in six files, five of them the same way. That
drift is invisible in the output -- each query is valid, each produces
triples, and the two IRIs for what should be one node simply never join. It
surfaces much later as a dangling reference or a duplicate entity, a long way
from the query that caused it.

Three findings, in descending order of how sure they are:

``TQL-001`` -- one target variable, several *structurally different*
expressions across files. Compared on a **skeleton**: the expression with
every ``?var`` replaced by ``?``. That distinction matters, and it is what
keeps this check honest. Two files minting ``?nodeid_IRI`` as
``CONCAT("exd:_Node_", ?a)`` and ``CONCAT("exd:_Node_", ?b)`` are almost
certainly fine -- they read the same column under two names. Two files minting
it as ``CONCAT("exd:_Node_", ?a)`` and
``CONCAT("exd:_Node_", REPLACE(?a, ?special, ""))`` are not fine: those
produce different IRIs for the same node whenever the value contains the
replaced character. Skeleton comparison flags the second and ignores the
first. Comparing raw expression text would report both and be ignored
accordingly.

``TQL-002`` -- a variable whose name follows the constructed-IRI convention
(``?something_IRI``) is used in the ``CONSTRUCT`` template but never bound
anywhere in the query. By that naming convention the variable is built, not
read from a column, so nothing will ever bind it and the triple it appears in
is silently dropped for every row.

``TQL-003`` -- the same, for a variable *not* following that convention.
Reported at Info, and usually not a defect at all: TARQL binds each CSV
header as a variable of the same name, so an unbound ``?roadname`` is
ordinarily just a column. It is listed because the only way to tell a column
from a typo is to read the CSV header, which is a reviewer's job, not this
module's.

Why not simply require every CONSTRUCT variable to be bound
-----------------------------------------------------------
Because TARQL's whole premise is the implicit column binding, that rule would
fire on nearly every well-formed query. Measured against a real ten-query
folder: 32 of 228 CONSTRUCT variables are unbound in their own file, and
exactly one of those is a genuine defect. Separating the two by naming
convention is what makes the finding actionable rather than a number nobody
reads.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import os
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from .. import io_utils
from ..checks.merge import ResultRow

# The vocabulary the BIND facts are published in, and the namespace their
# nodes are minted under. `tq:` is the schema, `tqd:` the data -- the same
# split every ontology in this suite's own examples keeps, and the reason
# a finding's focus node reads as a place in a file rather than a hash.
TQ = Namespace("https://semantechs.co.uk/ontology-quality/tarql/")
TQD = Namespace("https://semantechs.co.uk/ontology-quality/tarql/data/")

# The naming convention that says "this variable is constructed, not read from
# a column". Configurable because it is a convention, not a rule -- but it is
# a widespread one in TARQL folders, and without some such signal TQL-002 and
# TQL-003 cannot be told apart.
DEFAULT_CONSTRUCTED_SUFFIXES = ("_IRI", "_iri", "_URI", "_uri")

VARIABLE = re.compile(r"[?$]([A-Za-z_][A-Za-z0-9_]*)")
CONSTRUCT_KEYWORD = re.compile(r"\bCONSTRUCT\b", re.IGNORECASE)
WHERE_KEYWORD = re.compile(r"\bWHERE\b", re.IGNORECASE)


def strip_comments(text: str) -> str:
    """Blank out ``#`` comments, leaving everything else at its original
    offset so reported line numbers stay true.

    A naive ``#``-to-end-of-line strip is wrong on SPARQL twice over: ``#`` is
    the fragment separator in almost every RDF namespace, so ``<http://x#y>``
    would lose its local name, and a ``#`` inside a string literal (TARQL
    queries build IRIs out of exactly such literals) would truncate the
    expression. This scanner tracks IRI brackets and both quote styles, and
    only treats ``#`` as a comment outside them.
    """
    out = []
    i, n = 0, len(text)
    quote: str | None = None
    in_iri = False
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:      # escaped char inside a literal
                out.append(text[i + 1])
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                out.append(text[i - len(quote) + 1:i])
                quote = None
                continue
            i += 1
            continue
        if in_iri:
            out.append(ch)
            if ch == ">":
                in_iri = False
            i += 1
            continue
        if ch == "<" and re.match(r"<[^<>\s{}|\\^`\"]*>", text[i:]):
            in_iri = True
            out.append(ch)
            i += 1
            continue
        for q in ('"""', "'''", '"', "'"):
            if text.startswith(q, i):
                quote = q
                out.append(text[i:i + len(q)])
                i += len(q)
                break
        else:
            if ch == "#":
                j = text.find("\n", i)
                j = n if j == -1 else j
                out.append(" " * (j - i))     # preserve offsets
                i = j
                continue
            out.append(ch)
            i += 1
    return "".join(out)


def _matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at ``start``, ignoring brackets
    inside string literals."""
    depth = 0
    i, n = start, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = None
                continue
            i += 1
            continue
        matched_quote = False
        for q in ('"""', "'''", '"', "'"):
            if text.startswith(q, i):
                quote = q
                i += len(q)
                matched_quote = True
                break
        if matched_quote:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unbalanced {open_ch}{close_ch} from offset {start}")


def _blocks(text: str, keyword: re.Pattern) -> List[str]:
    blocks = []
    for match in keyword.finditer(text):
        brace = text.find("{", match.end())
        if brace == -1:
            continue
        try:
            end = _matching(text, brace, "{", "}")
        except ValueError:
            continue
        blocks.append(text[brace + 1:end])
    return blocks


def skeleton(expression: str) -> str:
    """The expression with every variable reduced to a bare ``?``, and
    whitespace collapsed -- what makes two BINDs "the same pattern".

    Keeping the literals and the function calls but dropping variable *names*
    is the whole point: a difference in which column feeds a template is
    ordinary (the same data is called ``?direction`` in one CSV and
    ``?directionName`` in another), while a difference in the template itself
    changes the IRI that comes out.
    """
    return " ".join(VARIABLE.sub("?", expression).split())


# Matches a call at the head of an expression, prefixed or not, so
# `tarql:expandPrefixedName(` and `CONCAT(` are both recognised.
CALL_HEAD = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?)\s*\(")

# What kind of RDF term an expression yields, keyed on the outermost call's
# local name so a prefixed call is recognised whatever prefix the query bound.
#
# The kinds are the ones a naming convention can be checked against. A TARQL
# folder marks a constructed variable by its suffix -- `?x_IRI` for a minted
# IRI, and some projects `?x_DT` for a value given a datatype -- and the
# check that convention needs is "does this expression actually produce that
# kind of term". `STRDT` was originally filed with the string functions here,
# which answered the `_IRI` question correctly by accident and made the `_DT`
# question unaskable: it does not produce a string, it produces a typed
# literal, and that distinction is the whole point of the convention.
#
# Deliberately not exhaustive. An unrecognised function means "cannot tell",
# published as the *absence* of a fact rather than as a guess, so a check
# reading this can never fire on something the parser did not actually
# recognise.
VALUE_KIND_BY_CALL = {
    # -> an IRI
    "iri": "IRI", "uri": "IRI", "expandprefixedname": "IRI",
    # -> a literal carrying an explicit datatype
    "strdt": "TypedLiteral",
    # -> a literal carrying a language tag
    "strlang": "LangLiteral",
    # -> a plain (xsd:string) literal
    "concat": "String", "str": "String", "substr": "String",
    "replace": "String", "ucase": "String", "lcase": "String",
    "strafter": "String", "strbefore": "String", "encode_for_uri": "String",
}


def outermost_call(expression: str) -> Optional[str]:
    """The function whose result *is* the expression, or None.

    None for a bare variable, a bare literal, or anything that is not a
    single wrapping call -- `f(a) + g(b)` has no outermost call in the sense
    that matters here, and saying so is better than picking one.
    """
    text = expression.strip()
    head = CALL_HEAD.match(text)
    if head is None:
        return None
    close = _matching(text, head.end() - 1, "(", ")")
    if close != len(text) - 1:
        return None                       # the call ends before the expression does
    return head.group(1)


def value_kind(expression: str) -> Optional[str]:
    """What kind of RDF term the expression yields -- ``"IRI"``,
    ``"TypedLiteral"``, ``"LangLiteral"``, ``"String"`` -- or None for
    "cannot tell from the query text".

    This is the fact the suffix conventions are really about, and computing
    it here rather than in the check is the point. SPARQL can only ask
    whether the expression *text* contains ``"IRI("`` -- a heuristic that
    misreads ``MYIRI(``, misses a bare string literal, and cannot tell the
    outermost call from a nested one. The parser already knows, because
    ``_matching`` had to bracket-match to find the expression at all.

    That is the division this module and ``resources/sparql/tarql/`` keep to:
    anything needing the *structure* of the query text is computed here and
    published as a fact; anything relating facts to each other is a query.
    The rule is not difficulty -- ``TQL-001``'s cross-file skeleton
    comparison is a nested aggregate and would be a perfectly good query --
    it is whether a ``FILTER`` would be re-deriving what the parse knew.

    None is returned generously. A bare variable may already hold an IRI
    from an earlier ``BIND``; an unrecognised function may do anything. A
    check reading this fact therefore never fires on an expression this
    module merely failed to understand, which is what lets such a check
    carry Violation severity.
    """
    text = expression.strip()
    call = outermost_call(text)
    if call is not None:
        return VALUE_KIND_BY_CALL.get(call.rsplit(":", 1)[-1].lower())
    if text[:1] in ('"', "'"):
        # A bare literal. `"x"^^xsd:date` and `"x"@en` carry their own kind;
        # anything else is a plain string.
        if "^^" in text:
            return "TypedLiteral"
        if re.search(r'"@[A-Za-z]', text) or re.search(r"'@[A-Za-z]", text):
            return "LangLiteral"
        return "String"
    return None                           # a bare variable: it may be anything


def produces_iri(expression: str) -> Optional[bool]:
    """``value_kind`` narrowed to the question ``TQL-004`` asks. None where
    the kind is unknown, so "not an IRI" is never inferred from silence."""
    kind = value_kind(expression)
    return None if kind is None else kind == "IRI"


@dataclass(frozen=True)
class BindStatement:
    source: str
    target: str
    expression: str
    line: int

    @property
    def skeleton(self) -> str:
        return skeleton(self.expression)

    @property
    def outermost_call(self) -> Optional[str]:
        return outermost_call(self.expression)

    @property
    def value_kind(self) -> Optional[str]:
        return value_kind(self.expression)

    @property
    def produces_iri(self) -> Optional[bool]:
        return produces_iri(self.expression)


@dataclass
class QueryFacts:
    source: str
    binds: List[BindStatement] = field(default_factory=list)
    construct_vars: Set[str] = field(default_factory=set)
    where_vars: Set[str] = field(default_factory=set)

    @property
    def bound(self) -> Set[str]:
        return {b.target for b in self.binds} | self.where_vars


def extract_binds(text: str, source: str) -> List[BindStatement]:
    """Every ``BIND(<expr> AS ?target)`` in ``text``.

    Bracket-matched rather than regexed to the first ``)``: the expressions
    this is built for nest several calls deep
    (``tarql:expandPrefixedName(CONCAT("x", REPLACE(?a, ?b, "_")))``), and a
    non-greedy regex stops at the wrong paren as soon as the ``AS`` clause is
    not the outermost thing in the statement.
    """
    binds = []
    for match in re.finditer(r"\bBIND\s*\(", text, re.IGNORECASE):
        open_paren = text.index("(", match.end() - 1)
        try:
            close = _matching(text, open_paren, "(", ")")
        except ValueError:
            continue
        inner = text[open_paren + 1:close]
        split = None
        depth = 0
        for m in re.finditer(r"\(|\)|\bAS\b", inner, re.IGNORECASE):
            token = m.group(0)
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
            elif depth == 0:
                split = m           # last top-level AS wins
        if split is None:
            continue
        target_match = VARIABLE.search(inner[split.end():])
        if target_match is None:
            continue
        binds.append(BindStatement(
            source=source,
            target=target_match.group(1),
            expression=" ".join(inner[:split.start()].split()),
            line=text.count("\n", 0, match.start()) + 1,
        ))
    return binds


def parse_query_facts(path: str) -> QueryFacts:
    text = strip_comments(io_utils.read_text(path))
    construct_vars: Set[str] = set()
    for block in _blocks(text, CONSTRUCT_KEYWORD):
        construct_vars |= set(VARIABLE.findall(block))
    where_vars: Set[str] = set()
    for block in _blocks(text, WHERE_KEYWORD):
        where_vars |= set(VARIABLE.findall(block))
    return QueryFacts(
        source=path,
        binds=extract_binds(text, path),
        construct_vars=construct_vars,
        where_vars=where_vars,
    )


@dataclass
class DriftGroup:
    """One target variable bound structurally differently across files."""
    target: str
    variants: List[Tuple[str, List[BindStatement]]]   # (skeleton, statements)

    @property
    def file_count(self) -> int:
        return len({b.source for _, stmts in self.variants for b in stmts})


@dataclass
class UnboundVariable:
    source: str
    variable: str
    looks_constructed: bool


@dataclass
class BindReport:
    queries: List[QueryFacts] = field(default_factory=list)
    drift: List[DriftGroup] = field(default_factory=list)
    unbound: List[UnboundVariable] = field(default_factory=list)
    shared_and_consistent: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def bind_count(self) -> int:
        return sum(len(q.binds) for q in self.queries)

    @property
    def is_clean(self) -> bool:
        return not self.drift and not any(u.looks_constructed for u in self.unbound)


def analyse(
    paths: Sequence[str],
    constructed_suffixes: Iterable[str] = DEFAULT_CONSTRUCTED_SUFFIXES,
) -> BindReport:
    suffixes = tuple(constructed_suffixes)
    report = BindReport(queries=[parse_query_facts(p) for p in paths])

    by_target: Dict[str, List[BindStatement]] = defaultdict(list)
    for query in report.queries:
        for bind in query.binds:
            by_target[bind.target].append(bind)

    for target, statements in sorted(by_target.items()):
        if len({b.source for b in statements}) < 2:
            continue                      # bound in one file only: nothing to compare
        by_skeleton: Dict[str, List[BindStatement]] = defaultdict(list)
        for bind in statements:
            by_skeleton[bind.skeleton].append(bind)
        if len(by_skeleton) > 1:
            report.drift.append(DriftGroup(
                target=target,
                variants=sorted(by_skeleton.items()),
            ))
        else:
            report.shared_and_consistent.append((target, len({b.source for b in statements})))

    for query in report.queries:
        for variable in sorted(query.construct_vars - query.bound):
            report.unbound.append(UnboundVariable(
                source=query.source,
                variable=variable,
                looks_constructed=variable.endswith(suffixes),
            ))
    return report


def bind_report_to_graph(
    report: BindReport,
    constructed_suffixes: Iterable[str] = DEFAULT_CONSTRUCTED_SUFFIXES,
) -> Graph:
    """``BindReport`` as RDF, so a TARQL check can be a query file.

    This exists to close a gap in how the suite is extended. A new SPARQL or
    SHACL check is a file plus a registry entry and no code at all, because
    the thing being checked is a graph and the runner needs to understand
    nothing about it. A new TARQL check needed Python, because the thing
    being checked -- the text of a `BIND` expression -- never became a graph:
    `tarql_visualiser` keeps each query's CONSTRUCT template and discards the
    WHERE clause before `sketch.ttl` exists.

    So publish the facts instead of the findings. Every `BIND` becomes a node
    carrying its target, its expression, its skeleton, its file and its line;
    every CONSTRUCT variable becomes a node saying whether it was bound and
    whether its name follows the constructed-IRI convention. A check over
    those is an ordinary `.rq` file in `resources/sparql/tarql/`, added
    exactly the way `STR-002` is added.

    The division of labour is the honest one rather than a compromise:
    skeletonisation is a parse and stays in Python, because SPARQL cannot do
    it. Once the skeleton is a literal in the graph, asking whether two files
    disagree about one is a `GROUP BY`. Python parses; SPARQL asks.

    Node IRIs are derived from the file's basename plus the line, so they are
    stable across runs and legible in a report -- a finding's focus node
    reads as the place to open, which is what a query-source finding has
    instead of a subject IRI. Two files with the same basename in different
    directories would collide; the path is on the query node so the
    distinction is not lost, and a folder holding two `road_to_rdf.rq` is its
    own problem.
    """
    suffixes = tuple(constructed_suffixes)
    graph = Graph(bind_namespaces="none")
    graph.bind("tq", TQ)
    graph.bind("tqd", TQD)
    graph.bind("xsd", XSD)

    for query in report.queries:
        name = os.path.basename(query.source)
        query_node = URIRef(TQD + quote(name, safe=""))
        graph.add((query_node, RDF.type, TQ.Query))
        graph.add((query_node, TQ.source, Literal(name)))
        graph.add((query_node, TQ.path, Literal(query.source)))

        for bind in query.binds:
            bind_node = URIRef(f"{query_node}/bind/{bind.line}")
            graph.add((bind_node, RDF.type, TQ.Bind))
            graph.add((bind_node, TQ.inQuery, query_node))
            graph.add((bind_node, TQ.target, Literal(bind.target)))
            graph.add((bind_node, TQ.expression, Literal(bind.expression)))
            graph.add((bind_node, TQ.skeleton, Literal(bind.skeleton)))
            graph.add((bind_node, TQ.line, Literal(bind.line, datatype=XSD.integer)))
            graph.add((bind_node, TQ.source, Literal(name)))

            # The two facts that need the parse rather than the text. A check
            # asking "does this build an IRI" would otherwise have to search
            # the expression for "IRI(", which misreads `MYIRI(`, misses a
            # bare string literal entirely, and cannot tell the outermost
            # call from a nested one. `_matching` already bracket-matched to
            # find the expression; throwing that away and regexing the result
            # is the mistake this whole graph exists to avoid.
            call = bind.outermost_call
            if call is not None:
                graph.add((bind_node, TQ.outermostCall, Literal(call)))
            kind = bind.value_kind
            if kind is not None:
                # Absent, never guessed, when the text does not settle it --
                # a bare variable may already hold an IRI from an earlier
                # BIND. A check reading this fact therefore cannot fire on
                # something the parser merely failed to recognise, which is
                # what lets one carry Violation severity.
                graph.add((bind_node, TQ.producesKind, Literal(kind)))

        # Both the bound and the unbound are published. A check about what a
        # query *does* bind is as reasonable as one about what it misses, and
        # emitting only the gaps would make the first kind unwritable.
        for variable in sorted(query.construct_vars):
            var_node = URIRef(f"{query_node}/var/{quote(variable, safe='')}")
            graph.add((var_node, RDF.type, TQ.ConstructVariable))
            graph.add((var_node, TQ.inQuery, query_node))
            graph.add((var_node, TQ.variable, Literal(variable)))
            graph.add((var_node, TQ.source, Literal(name)))
            graph.add((var_node, TQ.bound, Literal(variable in query.bound)))
            graph.add((var_node, TQ.constructed, Literal(variable.endswith(suffixes))))

    return graph


def bind_report_to_rows(report: BindReport) -> List[ResultRow]:
    """``BindReport`` as the same ``ResultRow`` every other check reports as.

    ``focus_node`` is the variable rather than an IRI: these findings are
    about query source, and there is no graph node to point at. The file (and
    line, where there is one) goes in ``path``, which is what a reviewer
    actually needs to open next.
    """
    rows: List[ResultRow] = []
    for group in report.drift:
        detail = "; ".join(
            f"{skel} [{', '.join(sorted({_name(b.source) for b in stmts}))}]"
            for skel, stmts in group.variants
        )
        rows.append(ResultRow(
            check_id="TQL-001", category="tarql",
            title="Variable bound by structurally different expressions across queries",
            severity="Warning",
            focus_node=f"?{group.target}",
            path=", ".join(sorted({_name(b.source) for _, stmts in group.variants for b in stmts})),
            value=None,
            message=(
                f"?{group.target} is bound in {group.file_count} query files by "
                f"{len(group.variants)} structurally different expressions: {detail}"
            ),
            remediation=(
                "Decide which expression is correct and use it in every file, or rename the "
                "variables so the two are not mistaken for one another. Differing only in which "
                "variable feeds the template is fine and is not reported; differing in the "
                "template itself means the same conceptual node gets two different IRIs."
            ),
            sources=["tarql"],
        ))

    for entry in report.unbound:
        if entry.looks_constructed:
            rows.append(ResultRow(
                check_id="TQL-002", category="tarql",
                title="Constructed-IRI variable used in CONSTRUCT but never bound",
                severity="Violation",
                focus_node=f"?{entry.variable}",
                path=_name(entry.source), value=None,
                message=(
                    f"?{entry.variable} is used in the CONSTRUCT template of {_name(entry.source)} "
                    "but is never bound by a BIND or matched in the WHERE clause. Its naming "
                    "convention says it is constructed rather than read from a CSV column, so "
                    "nothing will bind it and every triple using it is dropped."
                ),
                remediation=(
                    "Add the missing BIND, or correct the variable name if it should be reading a "
                    "column directly."
                ),
                sources=["tarql"],
            ))
        else:
            rows.append(ResultRow(
                check_id="TQL-003", category="tarql",
                title="CONSTRUCT variable not bound in the query",
                severity="Info",
                focus_node=f"?{entry.variable}",
                path=_name(entry.source), value=None,
                message=(
                    f"?{entry.variable} is used in the CONSTRUCT template of {_name(entry.source)} "
                    "but is not bound in the query. This is ordinarily correct -- TARQL binds each "
                    "CSV header as a variable of that name -- so it is reported only so a reviewer "
                    "can confirm the column exists."
                ),
                remediation=(
                    "Check the variable against the CSV header. If there is no such column, this is "
                    "a typo and the triple is silently dropped for every row."
                ),
                sources=["tarql"],
            ))
    return rows


def _name(path: str) -> str:
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def format_bind_report(report: BindReport, show_consistent: bool = True) -> str:
    """A reviewer-facing rendering: what to look at, in what order, with the
    competing expressions side by side so the judgement can be made without
    opening both files."""
    lines: List[str] = []
    lines.append("TARQL BIND review")
    lines.append("=" * 60)
    lines.append(
        f"{len(report.queries)} query file(s), {report.bind_count} BIND statement(s), "
        f"{len({b.target for q in report.queries for b in q.binds})} distinct target variable(s)."
    )
    lines.append("")

    lines.append(f"1. Variables bound differently across files ({len(report.drift)})")
    lines.append("-" * 60)
    if not report.drift:
        lines.append("   None. Every variable bound in more than one file uses the same pattern.")
    for group in report.drift:
        lines.append(f"   ?{group.target}  -- {len(group.variants)} patterns across {group.file_count} files")
        for skel, statements in group.variants:
            files = ", ".join(f"{_name(b.source)}:{b.line}" for b in sorted(statements, key=lambda b: b.source))
            lines.append(f"       {skel}")
            lines.append(f"           {files}")
    lines.append("")

    constructed = [u for u in report.unbound if u.looks_constructed]
    lines.append(f"2. Constructed-IRI variables never bound ({len(constructed)})")
    lines.append("-" * 60)
    if not constructed:
        lines.append("   None.")
    for entry in constructed:
        lines.append(f"   ?{entry.variable:<34} {_name(entry.source)}")
    lines.append("")

    columns = [u for u in report.unbound if not u.looks_constructed]
    lines.append(f"3. CONSTRUCT variables not bound in the query ({len(columns)})")
    lines.append("-" * 60)
    lines.append("   Expected to be CSV columns. Confirm each against its header.")
    by_file: Dict[str, List[str]] = defaultdict(list)
    for entry in columns:
        by_file[_name(entry.source)].append(entry.variable)
    for name in sorted(by_file):
        lines.append(f"   {name}")
        lines.append(f"       {', '.join('?' + v for v in sorted(by_file[name]))}")
    if not columns:
        lines.append("   None.")

    if show_consistent and report.shared_and_consistent:
        lines.append("")
        lines.append(f"4. Shared and consistent ({len(report.shared_and_consistent)})")
        lines.append("-" * 60)
        lines.append("   Bound in several files, always the same way -- no action needed.")
        for target, count in sorted(report.shared_and_consistent):
            lines.append(f"   ?{target:<34} {count} files")
    return "\n".join(lines)
