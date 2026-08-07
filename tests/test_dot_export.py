"""Tests for ontology_suite.sketch.dot_export -- particularly blank-node,
literal, and RDF-list handling, adapted from turtle-editor-viewer's
graph-generator.ts and create_class_diagrams.py (this suite's other
DOT-generating companion tools; see docs/MODELLING_PATTERN_CONSISTENCY.md
and docs/CLASS_DIAGRAMS.md). Two real things were caught building this:

1. word-wrapped literals' inserted "\\l" (Graphviz's left-justified-
   newline label syntax) was being backslash-escaped into literal "\\\\l"
   text instead of rendering as a line break.
2. blank nodes were rendered as dashed boxes labelled with their raw
   (unstable-across-parses) identifier -- changed to an unlabelled point,
   and rdf:first/rdf:rest list chains (owl:unionOf/intersectionOf/
   disjointWith) compacted into one record-shaped node with a port per
   member, both per explicit user feedback after seeing the dashed-box
   rendering in practice.
"""
import rdflib
from rdflib import RDF, RDFS, Graph, Literal, Namespace, OWL
from rdflib.collection import Collection

from ontology_suite.sketch import dot_export as de

EX = Namespace("https://example.org/demo/")


def _graph(*triples):
    g = Graph(bind_namespaces="none")
    g.bind("ex", EX)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    for t in triples:
        g.add(t)
    return g


def test_blank_node_is_an_unlabelled_amber_point_no_identifier_shown():
    b = rdflib.BNode()
    g = _graph((EX.Widget, EX.hasSpec, b))
    dot_text = de.graph_to_dot(g)
    # the node itself is declared with an empty label, amber colour, and a compact point shape ...
    assert f'"{b}" [label="", color="{de.BLANK_NODE_COLOR}", fontcolor="{de.BLANK_NODE_COLOR}", shape=point' in dot_text
    # ... and the raw id never appears inside any label="..." attribute
    # (it's still legitimately used as the DOT node identifier for edges to
    # reference, e.g. "ex:Widget" -> "<id>" -- that's expected and fine)
    assert f'label="{b}"' not in dot_text
    assert "_:" not in dot_text


def test_short_literal_is_not_wrapped():
    g = _graph((EX.Widget, EX.hasColor, Literal("red")))
    dot_text = de.graph_to_dot(g)
    assert 'label="red"' in dot_text
    assert "\\l" not in dot_text


def test_long_literal_is_word_wrapped_with_real_graphviz_linebreaks():
    long_text = "A very long description that should definitely wrap across more than one line in the rendered box"
    g = _graph((EX.Widget, EX.hasDescription, Literal(long_text)))
    dot_text = de.graph_to_dot(g)
    # exactly one literal backslash followed by "l" per break -- not a
    # doubled/escaped "\\\\l" (the regression this test guards)
    assert "\\l" in dot_text
    assert "\\\\l" not in dot_text
    # re-joining on the Graphviz line-break marker recovers the original text
    label_line = next(line for line in dot_text.splitlines() if "hasDescription" not in line and long_text[:20] in line)
    recovered = label_line.split('label="', 1)[1].split('", color=', 1)[0].replace("\\l", " ")
    assert recovered == long_text


def test_literal_with_a_quote_is_escaped_safely():
    g = _graph((EX.Widget, EX.hasNote, Literal('he said "hello"')))
    dot_text = de.graph_to_dot(g)
    assert '\\"hello\\"' in dot_text
    # every quote that opens a DOT string attribute must be balanced
    assert dot_text.count('"') % 2 == 0


def test_literal_node_is_an_ellipse_iri_nodes_are_boxes_bnodes_are_points():
    b = rdflib.BNode()
    g = _graph(
        (EX.Widget, EX.hasColor, Literal("red")),
        (EX.Widget, RDF.type, EX.Product),
        (EX.Widget, EX.hasSpec, b),
    )
    dot_text = de.graph_to_dot(g)
    assert f'"red" [label="red", color="{de.LITERAL_BORDER_COLOR}", fontcolor="black", shape=ellipse];' in dot_text
    assert '"https://example.org/demo/Product" [label="ex:Product", color="black", fontcolor="black", shape=box];' in dot_text
    assert f'shape=point' in dot_text


def test_rdf_type_renders_as_a():
    g = _graph((EX.Widget, RDF.type, EX.Product))
    dot_text = de.graph_to_dot(g)
    assert 'label="a"' in dot_text
    assert "ns1:type" not in dot_text


def test_rdf_nil_renders_as_empty_parens():
    g = _graph((EX.Widget, OWL.unionOf, RDF.nil))
    dot_text = de.graph_to_dot(g)
    assert 'label="()"' in dot_text


# --- RDF list (rdf:first/rdf:rest) compaction --------------------------------

def test_union_list_compacts_into_one_record_node_with_a_port_per_member():
    g = _graph()
    lst = rdflib.BNode()
    Collection(g, lst, [EX.Vehicle, EX.Trailer, EX.Motorcycle])
    g.add((EX.VehicleOrTrailer, OWL.unionOf, lst))

    dot_text = de.graph_to_dot(g)
    assert 'shape=record, style=rounded, label="<p0>|<p1>|<p2>"' in dot_text
    assert f'"{lst}":p0 -> "https://example.org/demo/Vehicle"' in dot_text
    assert f'"{lst}":p1 -> "https://example.org/demo/Trailer"' in dot_text
    assert f'"{lst}":p2 -> "https://example.org/demo/Motorcycle"' in dot_text
    # the incoming edge from outside lands on the whole record, not a port
    assert f'"https://example.org/demo/VehicleOrTrailer" -> "{lst}" [label="owl:unionOf"' in dot_text
    # no leftover rdf:first/rdf:rest edges -- the list is fully represented by the record + ports
    assert "rdf:first" not in dot_text and "rdf:rest" not in dot_text
    assert "_:" not in dot_text


def test_list_members_are_rendered_with_their_own_normal_node_styling():
    """A union member that's itself a literal or gets a colour override
    still goes through the normal node pipeline (ellipse/box, colours) --
    the record is just a fan-out junction, not a dead end."""
    g = _graph()
    lst = rdflib.BNode()
    Collection(g, lst, [EX.Vehicle, Literal("literal member")])
    g.add((EX.X, OWL.unionOf, lst))
    dot_text = de.graph_to_dot(g, node_colors={str(EX.Vehicle): "red"})
    assert '"https://example.org/demo/Vehicle" [label="ex:Vehicle", color="red"' in dot_text
    assert f'"literal member" [label="literal member", color="{de.LITERAL_BORDER_COLOR}", fontcolor="black", shape=ellipse];' in dot_text


def test_list_record_node_defaults_to_the_same_amber_as_other_blank_nodes():
    """A list cell is a blank node too (see BLANK_NODE_COLOR) and a
    syntactic construct (how a union/intersection is spelled in RDF), not
    a modelled entity -- it gets the same amber colour as a plain blank
    node rather than the plain black/gray every other node defaults to,
    so anything anonymous reads as visually distinct at a glance."""
    g = _graph()
    lst = rdflib.BNode()
    Collection(g, lst, [EX.Vehicle])
    g.add((EX.X, OWL.unionOf, lst))
    dot_text = de.graph_to_dot(g)
    assert (
        f'"{lst}" [shape=record, style=rounded, label="<p0>", '
        f'color="{de.BLANK_NODE_COLOR}", fontcolor="{de.BLANK_NODE_COLOR}"];'
    ) in dot_text


def test_list_record_node_colour_can_still_be_overridden():
    g = _graph()
    lst = rdflib.BNode()
    Collection(g, lst, [EX.Vehicle])
    g.add((EX.X, OWL.unionOf, lst))
    dot_text = de.graph_to_dot(g, node_colors={str(lst): "red"})
    assert f'"{lst}" [shape=record, style=rounded, label="<p0>", color="red", fontcolor="red"];' in dot_text


def test_nested_list_renders_as_nested_record_nodes():
    g = _graph()
    inner = rdflib.BNode()
    Collection(g, inner, [EX.A, EX.B])
    outer = rdflib.BNode()
    Collection(g, outer, [inner, EX.C])
    g.add((EX.X, OWL.unionOf, outer))

    dot_text = de.graph_to_dot(g)
    assert dot_text.count("shape=record") == 2
    assert f'"{outer}":p0 -> "{inner}"' in dot_text
    assert f'"{inner}":p0 -> "https://example.org/demo/A"' in dot_text


def test_single_plain_blank_node_is_not_mistaken_for_a_list():
    """A blank node with predicates other than exactly {rdf:first,
    rdf:rest} (e.g. a plain owl:Restriction) must render as a normal
    point, not get swept into list-compaction logic."""
    b = rdflib.BNode()
    g = _graph((EX.Widget, RDFS.subClassOf, b), (b, RDF.type, OWL.Restriction), (b, OWL.onProperty, EX.hasColor))
    dot_text = de.graph_to_dot(g)
    assert "shape=record" not in dot_text
    assert f'"{b}" [label="", color="{de.BLANK_NODE_COLOR}", fontcolor="{de.BLANK_NODE_COLOR}", shape=point' in dot_text


def test_edge_and_node_color_overrides_apply():
    g = _graph((EX.Widget, EX.hasColor, EX.Red))
    dot_text = de.graph_to_dot(
        g,
        edge_colors={(str(EX.Widget), str(EX.hasColor), str(EX.Red)): "red"},
        node_colors={str(EX.Red): "red"},
    )
    assert '"https://example.org/demo/Red" [label="ex:Red", color="red"' in dot_text
    assert 'label="ex:hasColor", color="red"' in dot_text


def test_write_dot_writes_a_file(tmp_path):
    g = _graph((EX.Widget, RDF.type, EX.Product))
    out_path = tmp_path / "g.dot"
    written = de.write_dot(g, out_path)
    assert written == out_path
    assert out_path.read_text(encoding="utf-8").startswith("digraph sketch {")
