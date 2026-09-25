"""The namespace legend is written in the tool's namespace, not the project's.

`write_turtle` describes each prefix binding as a data triple so the mapping
is visible in the graph and not only in the `@prefix` directives. The
predicate was written as the CURIE `:isRepresentedBy`, and `:` is bound to the
scratch namespace *only when no query declares an empty prefix*. Most queries
declare one.

So for most real query folders the tool's own predicate landed in the
project's namespace, where the conformance layer reported it as a property
used but never declared -- a finding about a term the project had never
written, in a file it had never written, which nothing it could change would
make go away. `graph_quality.default_ignored_predicates` was supposed to
suppress exactly this and resolved the predicate against the scratch
namespace, so it matched only in the case that almost never happens.

This is the same root cause as the per-row entity misreporting fixed in
0.14.3: a scratch term rendered with whatever the query calls `:`.
"""
import rdflib

from ontology_suite.sketch import graph_quality, tarql_visualiser as tv

QUERY_WITH_EMPTY_PREFIX = """
prefix : <https://example.org/project/model#>
CONSTRUCT { ?thing_IRI a :Thing . }
WHERE { BIND(IRI(CONCAT("https://example.org/d/", ?id)) AS ?thing_IRI) }
"""

QUERY_WITHOUT_EMPTY_PREFIX = """
prefix ex: <https://example.org/project/model#>
CONSTRUCT { ?thing_IRI a ex:Thing . }
WHERE { BIND(IRI(CONCAT("https://example.org/d/", ?id)) AS ?thing_IRI) }
"""

SCRATCH = tv.scratch_namespace()
LEGEND = rdflib.URIRef(SCRATCH + "isRepresentedBy")
PROJECT = rdflib.URIRef("https://example.org/project/model#isRepresentedBy")


def sketch_graph(tmp_path, query_text, name="q.rq"):
    (tmp_path / name).write_text(query_text, encoding="utf-8")
    out = tmp_path / "sketch.ttl"
    tv.write_turtle([tv.parse_query(str(tmp_path / name))], str(out))
    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    return graph


def test_the_legend_predicate_is_the_tools_own_whatever_the_query_calls_colon(tmp_path):
    graph = sketch_graph(tmp_path, QUERY_WITH_EMPTY_PREFIX)
    assert (None, LEGEND, None) in graph
    assert (None, PROJECT, None) not in graph, (
        "the legend predicate landed in the project's namespace, where the "
        "conformance layer will report it as an undeclared property")


def test_it_is_the_same_predicate_when_no_empty_prefix_is_declared(tmp_path):
    """The case that always worked keeps working, and produces the same IRI
    as the case that did not -- one predicate, not two."""
    graph = sketch_graph(tmp_path, QUERY_WITHOUT_EMPTY_PREFIX)
    assert (None, LEGEND, None) in graph


def test_the_ignore_set_matches_what_was_written(tmp_path):
    """The two have to agree, and this is the pair that did not. The ignore
    set resolved against the scratch namespace either way, so for a query
    with an empty prefix it matched nothing at all."""
    ignored = graph_quality.default_ignored_predicates()
    graph = sketch_graph(tmp_path, QUERY_WITH_EMPTY_PREFIX)

    legend_predicates = {p for p in graph.predicates() if "isRepresentedBy" in str(p)}
    assert legend_predicates, "no legend triple was written at all"
    assert {str(p) for p in legend_predicates} <= {str(i) for i in ignored}


def test_a_caller_supplied_predicate_is_left_alone(tmp_path):
    """Passing a full IRI means it. Only the `:local` form is resolved, and
    only because that form is the tool naming its own vocabulary."""
    (tmp_path / "q.rq").write_text(QUERY_WITH_EMPTY_PREFIX, encoding="utf-8")
    out = tmp_path / "sketch.ttl"
    tv.write_turtle([tv.parse_query(str(tmp_path / "q.rq"))], str(out),
                    namespace_predicate="<https://example.org/mine#legend>")
    graph = rdflib.Graph()
    graph.parse(out, format="turtle")
    assert (None, rdflib.URIRef("https://example.org/mine#legend"), None) in graph
