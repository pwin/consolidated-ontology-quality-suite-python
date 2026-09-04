"""
turtle_parser.py

Turtle parsing for the ontology documentation generator, built on
rdflib rather than a hand-written tokenizer/parser. This module is a
thin adapter: it parses Turtle with rdflib and converts the result into
the same (subject, predicate, object) triple list and term types
(`IRI`, `BNode`, `Literal`, `Collection`) that the rest of this project
(`extract_ontology_data.py`) already consumes, so no other file needed
to change.

Two things rdflib does differently from what `extract_ontology_data.py`
expects, both handled below:

  - rdflib expands RDF collections (`( ... )`, e.g. inside
    `owl:intersectionOf`) into an `rdf:first`/`rdf:rest` blank-node
    chain rather than keeping them as an ordered list. `_collapse_lists`
    reconstitutes each such chain into a `Collection` (an ordered
    Python list of terms) in-place, and the internal chain triples are
    dropped from the output - matching the shape
    `extract_ontology_data.py`'s graph traversal (e.g.
    `render_class_expression`) expects.
  - rdflib's default `Graph` auto-binds a large set of well-known
    prefixes (skos, foaf, dcat, ...) whether or not they appear in the
    source file, which would pollute the "namespaces used by this
    ontology" list downstream. Parsing with `bind_namespaces="none"`
    limits `graph.namespaces()` to exactly the `@prefix` declarations
    present in the source text.
"""

from rdflib import Graph as _RDFLibGraph
from rdflib import BNode as _RDFBNode
from rdflib import URIRef as _RDFURIRef
from rdflib import Literal as _RDFLiteral
from rdflib.namespace import RDF as _RDF


# ---------------------------------------------------------------------------
# Term types
# ---------------------------------------------------------------------------

class IRI(str):
    """An absolute IRI. Subclasses str so it can be used directly as a
    dict key / compared naturally, while remaining distinguishable from
    a plain Literal value via isinstance()."""
    __slots__ = ()

    def __repr__(self):
        return f"<{str(self)}>"


class BNode(str):
    """A blank node, identified by a generated or parsed label."""
    __slots__ = ()

    def __repr__(self):
        return f"_:{str(self)}"


class Literal:
    __slots__ = ("value", "lang", "datatype")

    def __init__(self, value, lang=None, datatype=None):
        self.value = value
        self.lang = lang
        self.datatype = datatype

    def __repr__(self):
        if self.lang:
            return f'"{self.value}"@{self.lang}'
        if self.datatype:
            return f'"{self.value}"^^{self.datatype}'
        return f'"{self.value}"'

    def __eq__(self, other):
        return (
            isinstance(other, Literal)
            and self.value == other.value
            and self.lang == other.lang
            and self.datatype == other.datatype
        )

    def __hash__(self):
        return hash((self.value, self.lang, self.datatype))


class Collection(list):
    """An RDF collection `( a b c )`, kept as an ordered list of terms
    rather than expanded into an rdf:first/rdf:rest blank-node chain."""
    __slots__ = ()


RDF_TYPE = IRI(str(_RDF.type))


# ---------------------------------------------------------------------------
# rdflib term -> our term types
# ---------------------------------------------------------------------------

def _convert_term(node, graph, list_heads, cache):
    """Convert a single rdflib node into an IRI/BNode/Literal/Collection,
    recursively expanding RDF-collection blank nodes into Collections."""
    if node is None:
        return None

    if isinstance(node, _RDFLiteral):
        datatype = IRI(str(node.datatype)) if node.datatype else None
        return Literal(str(node), lang=node.language, datatype=datatype)

    if node == _RDF.nil:
        return Collection()

    if isinstance(node, _RDFBNode):
        if node in list_heads:
            if node in cache:
                return cache[node]  # guards against a malformed recursive list
            collection = Collection()
            cache[node] = collection
            cursor = node
            seen = set()
            while cursor is not None and cursor != _RDF.nil:
                if cursor in seen:
                    break  # malformed/recursive rdf:rest chain - stop rather than loop forever
                seen.add(cursor)
                item = graph.value(cursor, _RDF.first)
                collection.append(_convert_term(item, graph, list_heads, cache))
                cursor = graph.value(cursor, _RDF.rest)
            return collection
        return BNode(str(node))

    if isinstance(node, _RDFURIRef):
        return IRI(str(node))

    return IRI(str(node))


def _list_head_nodes(graph):
    """Blank nodes that are cells of an rdf:first/rdf:rest collection
    chain. Their own first/rest/nil-chain triples are internal
    plumbing and are excluded from the final triple list once the
    collection has been reconstituted by _convert_term."""
    heads = set()
    for s in graph.subjects(_RDF.first, None):
        heads.add(s)
    for s in graph.subjects(_RDF.rest, None):
        heads.add(s)
    return heads


def _to_triples(graph):
    list_heads = _list_head_nodes(graph)
    cache = {}
    triples = []
    for s, p, o in graph:
        if s in list_heads:
            # internal rdf:first/rdf:rest chain plumbing, already folded
            # into a Collection wherever it's referenced as an object
            continue
        subj = _convert_term(s, graph, list_heads, cache)
        pred = _convert_term(p, graph, list_heads, cache)
        obj = _convert_term(o, graph, list_heads, cache)
        triples.append((subj, pred, obj))
    return triples


def _to_prefixes(graph):
    return {str(prefix): IRI(str(namespace)) for prefix, namespace in graph.namespaces()}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_turtle(text, source=None):
    """Parse RDF text, returning (triples, prefixes) where triples is
    a list of (subject, predicate, object) tuples using the IRI/BNode/
    Literal/Collection term types above, and prefixes is a
    {prefix: IRI} dict of exactly the prefix declarations present in
    the source text.

    Named for Turtle because that is what `--ontology` is, and Turtle stays
    the assumption when nothing says otherwise. `source` is the path the text
    came from, when there is one: `--ref` takes whatever serialisation the
    upstream vocabulary publishes, and FOAF publishes RDF/XML. Handing that
    to the Turtle parser crashed on the XML comment header -- a real report,
    and an unhelpful one, since the error named a Turtle syntax problem in a
    file containing no Turtle.

    `io_utils.resolve_format` decides: the extension's guess, overridden when
    the content plainly disagrees. Bytes are evidence, an extension is only a
    claim -- and `.owl` files written in Turtle are common enough that the
    override earns its keep in both directions.
    """
    graph = _RDFLibGraph(bind_namespaces="none")
    if source is None:
        graph.parse(data=text, format="turtle")
    else:
        from .. import io_utils
        graph.parse(data=text, format=io_utils.resolve_format(source, text.encode("utf-8")))
    return _to_triples(graph), _to_prefixes(graph)
