"""Renders an rdflib graph -- typically a TARQL/oxi-gen CONSTRUCT-template
sketch (`tarql_visualiser.py`/`prefix_alignment.build_sketch_graph`) or a
class's own concise bounded description (`docgen.class_diagrams`) -- as a
Graphviz DOT digraph, with optional per-triple/per-node colour overrides.

This is deliberately a plain, dependency-free text renderer (no `graphviz`/
`pydot` package, no subprocess call to `dot`) -- it produces a `.dot` file;
turning that into a picture is a separate, optional step
(`dot -Tsvg file.dot -o file.svg`, or paste it into any DOT viewer), same
spirit as `tarql_visualiser.write_turtle` producing a `.ttl` file rather
than trying to also be a Turtle viewer.

Used by `pattern_consistency.py` to turn a list of consistency-gap findings
into a picture instead of a text list (`docs/MODELLING_PATTERN_CONSISTENCY.md`)
and by `docgen.class_diagrams` for per-class diagrams (`docs/CLASS_DIAGRAMS.md`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import rdflib
from rdflib import RDF

OK_COLOR = "darkgreen"
GAP_COLOR = "red"
NEUTRAL_COLOR = "gray40"
BLANK_NODE_COLOR = "#C97A2B"  # the same amber docgen's HTML template uses for its
                              # own "annotation hint, not an OWL axiom" styling
                              # (--amber in templates/documentation-template.html)
                              # -- reused here for every blank node (a plain point
                              # as much as a list/union record -- a list cell IS a
                              # blank node), so anything anonymous reads as the
                              # same "syntactic construct, not a modelled entity"
                              # kind of thing, consistently, across every diagram
LITERAL_BORDER_COLOR = "blue"  # matches turtle-editor-viewer's own literal
                                # convention -- border only, not the text, so a
                                # node_colors override (red/green) still reads
                                # clearly against the default black label text

TripleKey = Tuple[str, str, str]


def _word_wrap(text: str, max_width: int = 40) -> str:
    """Wrap long literal text across multiple DOT label lines (`\\l` is
    Graphviz's own left-justified-newline label syntax) -- otherwise one
    long literal balloons its node's box across the whole picture. Adapted
    from `turtle-editor-viewer`'s `graph-generator.ts` (this suite's other
    DOT-capable companion tool, see the README's "Companion tools"
    section) -- same idea, ported to Python.
    """
    if len(text) <= max_width:
        return text
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\\l".join(lines)


def _label(term: rdflib.term.Identifier, graph: rdflib.Graph) -> str:
    if isinstance(term, rdflib.Literal):
        return _word_wrap(str(term))
    if isinstance(term, rdflib.BNode):
        # No identifier text: rdflib mints a fresh, unstable blank-node id
        # on every parse (the same root cause LOG-004 in this suite's
        # checks/ registry had to work around), so it was never meaningful
        # to compare across two runs anyway -- and a blank node is
        # structurally just a joint connecting real content (an
        # owl:Restriction's property/value, a list cell, ...), not an
        # entity worth naming. It renders as a small unlabelled point (see
        # _node_shape) instead, matching create_class_diagrams.py's
        # own `shape="point"` convention for the same reason.
        return ""
    if term == RDF.type:
        # CONSTRUCT templates write the Turtle "a" shorthand, never the curie
        # "rdf:type" -- tarql_visualiser never sees "rdf" as a used prefix,
        # so the sketch file never declares it and rdflib auto-mints an
        # opaque "ns1:type" qname for it. "a" is the standard, recognizable
        # rendering everywhere else RDF triples get displayed.
        return "a"
    if term == RDF.nil:
        return "()"  # Turtle's own shorthand for the empty list
    try:
        return graph.qname(term)
    except Exception:
        return str(term)


def _node_shape(term: rdflib.term.Identifier) -> str:
    """Shape for a node, independent of its colour. An *explicit*
    `node_colors` override (consistency status: red/green) always takes
    priority over the term-kind default colour a plain literal/blank node
    otherwise gets (see `ensure_node` in `graph_to_dot`) -- so shape
    (literal/blank node/IRI) and colour don't collide on one visual
    channel when both matter at once.
    """
    if isinstance(term, rdflib.Literal):
        return "ellipse"
    if isinstance(term, rdflib.BNode):
        return "point"
    return "box"


def _escape(text: str) -> str:
    # Deliberately doesn't escape backslashes: _word_wrap() inserts literal
    # "\l" (Graphviz's own left-justified-newline label syntax) into
    # literal labels, and escaping it to "\\l" here would print it as text
    # instead of rendering a line break -- confirmed as a real bug by
    # actually rendering a wrapped literal through `dot` and seeing "\l"
    # show up verbatim in the box instead of a line break. Only quotes
    # need escaping to keep the DOT string well-formed (same as
    # turtle-editor-viewer's own escapeDot(), this suite's other
    # DOT-generating companion tool).
    return text.replace('"', '\\"')


# ---------------------------------------------------------------------------
# RDF list (owl:unionOf / intersectionOf / disjointWith / ...) compaction:
# a chain of anonymous rdf:first/rdf:rest cells renders as one compact
# record-shaped node with a port per member, instead of one dashed/point
# node and two edges (first, rest) per cell -- the same technique both
# create_class_diagrams.py and turtle-editor-viewer's graph-generator.ts
# use (isListNode/renderList), ported here so every diagram this suite
# produces gets it, not just per-class ones. Genuinely more than a cosmetic
# nicety for owl:unionOf/intersectionOf/disjointWith, which gist-style
# ontologies use constantly -- the un-compacted rendering of a 3-member
# union is a zigzag of six extra nodes and edges that obscures the one
# thing worth seeing (what the union's members are).
# ---------------------------------------------------------------------------

def _is_list_cell(graph: rdflib.Graph, node: rdflib.term.Identifier) -> bool:
    if not isinstance(node, rdflib.BNode):
        return False
    preds = {p for p, _ in graph.predicate_objects(node)}
    return preds == {RDF.first, RDF.rest}


def _list_roots(graph: rdflib.Graph) -> Set[rdflib.BNode]:
    """List cells that start a chain -- i.e. never themselves the
    rdf:rest object of another list cell in this graph. Determined this
    way (not "the first list cell some traversal happens to reach") so
    rendering doesn't depend on dict/set iteration order the way
    create_class_diagrams.py's equivalent does."""
    cells = {s for s in set(graph.subjects()) if _is_list_cell(graph, s)}
    continuations = {
        o for s in cells for p, o in graph.predicate_objects(s) if p == RDF.rest and o in cells
    }
    return cells - continuations


def _walk_list(graph: rdflib.Graph, head: rdflib.term.Identifier) -> List[rdflib.term.Identifier]:
    members: List[rdflib.term.Identifier] = []
    node = head
    visited: Set[rdflib.term.Identifier] = set()
    while node is not None and node != RDF.nil and node not in visited:
        visited.add(node)
        pairs = list(graph.predicate_objects(node))
        first = next((o for p, o in pairs if p == RDF.first), None)
        rest = next((o for p, o in pairs if p == RDF.rest), None)
        if first is not None:
            members.append(first)
        node = rest
    return members


def graph_to_dot(
    graph: rdflib.Graph,
    *,
    edge_colors: Optional[Dict[TripleKey, str]] = None,
    node_colors: Optional[Dict[str, str]] = None,
    default_edge_color: str = NEUTRAL_COLOR,
    default_node_color: str = "black",
    graph_name: str = "sketch",
) -> str:
    """Render `graph` as a Graphviz DOT digraph string.

    `edge_colors` maps `(subject, predicate, object)` (each `str()`-ed,
    matching rdflib's own triple members) to a Graphviz colour name, for
    triples that should be drawn in a specific colour -- e.g. red for one
    a consistency check flagged, green for one confirmed to resolve.
    `node_colors` does the same per-node (keyed by `str()`-ed term).
    Triples/nodes not present in those maps use the default colours.
    `rdf:first`/`rdf:rest` list chains are rendered compactly (see the
    module docstring above) rather than node-per-cell.
    """
    lines = [
        f"digraph {graph_name} {{",
        "  rankdir=LR;",
        '  node [shape=box, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]

    seen_nodes: Set[str] = set()
    list_roots = _list_roots(graph)
    root_members = {root: _walk_list(graph, root) for root in list_roots}
    consumed_cells: Set[rdflib.term.Identifier] = set()
    for root, members in root_members.items():
        node = root
        while node is not None and node != RDF.nil and node not in consumed_cells:
            consumed_cells.add(node)
            rest = next((o for p, o in graph.predicate_objects(node) if p == RDF.rest), None)
            node = rest

    def ensure_node(term):
        key = str(term)
        if key in seen_nodes:
            return
        seen_nodes.add(key)
        shape = _node_shape(term)
        override = (node_colors or {}).get(key)
        if override is not None:
            # an explicit override (e.g. red/green consistency status)
            # always wins over any term-kind default, for both the border
            # and the text -- it's a more important signal than "this
            # happens to be a literal/blank node".
            border = font = override
        elif isinstance(term, rdflib.Literal):
            border, font = LITERAL_BORDER_COLOR, default_node_color
        elif isinstance(term, rdflib.BNode):
            border = font = BLANK_NODE_COLOR
        else:
            border = font = default_node_color
        label = _escape(_label(term, graph))
        extra = ", width=0.15, height=0.15, fixedsize=false" if shape == "point" else ""
        lines.append(f'  "{_escape(key)}" [label="{label}", color="{border}", fontcolor="{font}", shape={shape}{extra}];')

    def ensure_list_node(root):
        key = str(root)
        if key in seen_nodes:
            return
        seen_nodes.add(key)
        members = root_members[root]
        # amber + rounded, not the plain default -- a list cell is a
        # blank node too (see BLANK_NODE_COLOR), and syntactic (how a
        # union/intersection is spelled in RDF) rather than a modelled
        # entity, same distinction the "hint" badges in docgen's own HTML
        # template draw with the same colour.
        color = (node_colors or {}).get(key, BLANK_NODE_COLOR)
        ports = "|".join(f"<p{i}>" for i in range(len(members))) if members else " "
        lines.append(
            f'  "{_escape(key)}" [shape=record, style=rounded, label="{ports}", '
            f'color="{color}", fontcolor="{color}"];'
        )
        for i, member in enumerate(members):
            if member in list_roots:
                ensure_list_node(member)
            else:
                ensure_node(member)
            lines.append(f'  "{_escape(key)}":p{i} -> "{_escape(str(member))}" [color="{default_edge_color}"];')

    for root in list_roots:
        ensure_list_node(root)

    for s, p, o in sorted(graph, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        if s in consumed_cells:
            continue  # fully represented by its list record + port edges above
        ensure_node(s)
        if o in consumed_cells:
            if o not in list_roots:
                continue  # a non-root list cell referenced from outside its own chain -- not expected in well-formed data, skip rather than crash
            ensure_list_node(o)
        else:
            ensure_node(o)
        color = (edge_colors or {}).get((str(s), str(p), str(o)), default_edge_color)
        plabel = _escape(_label(p, graph))
        lines.append(
            f'  "{_escape(str(s))}" -> "{_escape(str(o))}" '
            f'[label="{plabel}", color="{color}", fontcolor="{color}"];'
        )

    lines.append("}")
    return "\n".join(lines)


def write_dot(graph: rdflib.Graph, out_path: str | Path, **kwargs) -> Path:
    out_path = Path(out_path)
    out_path.write_text(graph_to_dot(graph, **kwargs), encoding="utf-8")
    return out_path
