"""Two facts a mapping *set* needs, which a merged graph cannot hold.

Every check over a folder of TARQL queries used to be asked of one merged
graph: `build_sketch_graph` concatenates every file's CONSTRUCT template, and
`bind_report_to_graph` published each BIND without the query's prefix table.
Both are the right shape for "what does this mapping set build". Neither can
answer a question about the set disagreeing with itself:

  * one class built with a name by one file and without it by another is
    indistinguishable, once merged, from one class built one way;
  * one prefix bound to two namespaces by two files is invisible, because
    each file is internally consistent and the prefixes were never published
    at all -- downstream the term-level checks report an undeclared term in
    whichever file is wrong and never mention the other half.

So this pins the facts, not the checks that read them: a named graph per
query file whose name joins to the BIND facts, and a PrefixBinding node per
PREFIX declaration.
"""
import os

import pytest
import rdflib

from ontology_suite.sketch import bind_analysis, prefix_alignment

QUERIES = [
    ("sites.rq", """
prefix : <https://example.org/m#>
CONSTRUCT { ?site_IRI a :Site ; :siteName ?name . }
WHERE { BIND(IRI(CONCAT("https://example.org/d/site-", ?id)) AS ?site_IRI) }
"""),
    ("readings.rq", """
prefix :    <https://example.org/m#>
prefix ext: <https://example.org/other#>
CONSTRUCT { ?site_IRI a :Site . }
WHERE { BIND(IRI(CONCAT("https://example.org/d/site-", ?id)) AS ?site_IRI) }
"""),
]


def per_file_graphs(dataset):
    """The named graphs, keyed by file, without the default one.

    dataset.graphs() yields the default graph too, and it holds the union --
    so a caller who forgets to exclude it sees every file's triples twice
    over and concludes the files agree. Worth a helper rather than a line
    repeated in four tests.
    """
    return {os.path.basename(str(g.identifier)): g for g in dataset.graphs()
            if str(g.identifier).startswith(str(bind_analysis.TQD))}


@pytest.fixture
def query_dir(tmp_path):
    for name, text in QUERIES:
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_each_query_file_gets_its_own_named_graph(query_dir):
    dataset = prefix_alignment.build_sketch_dataset([query_dir], "*.rq")
    named = per_file_graphs(dataset)
    assert set(named) == {"sites.rq", "readings.rq"}

    site = rdflib.URIRef("https://example.org/m#Site")
    name_property = rdflib.URIRef("https://example.org/m#siteName")
    built_with_name = {f for f, g in named.items()
                       if any(g.triples((None, name_property, None)))}
    built_at_all = {f for f, g in named.items()
                    if any(g.triples((None, rdflib.RDF.type, site)))}

    # The point of the whole fixture: both files build :Site, only one gives
    # it a name, and that difference survives into the dataset.
    assert built_at_all == {"sites.rq", "readings.rq"}
    assert built_with_name == {"sites.rq"}


def test_the_default_graph_still_holds_the_union(query_dir):
    """A check written against build_sketch_graph keeps working when handed
    a dataset instead, which is what makes this addition safe to adopt."""
    dataset = prefix_alignment.build_sketch_dataset([query_dir], "*.rq")
    merged = prefix_alignment.build_sketch_graph([query_dir], "*.rq")
    default = getattr(dataset, "default_graph", None) or dataset.default_context
    assert set(default) == set(merged)


def test_graph_names_join_to_the_bind_facts(query_dir):
    """The two graphs are useful together -- what a file builds, and what its
    BINDs fill it with -- so they have to agree on what a file is called."""
    dataset = prefix_alignment.build_sketch_dataset([query_dir], "*.rq")
    facts = bind_analysis.bind_report_to_graph(
        bind_analysis.analyse([str(query_dir / name) for name, _ in QUERIES]))

    sketch_names = {str(g.identifier) for g in per_file_graphs(dataset).values()}
    query_nodes = {str(s) for s in facts.subjects(rdflib.RDF.type, bind_analysis.TQ.Query)}
    assert query_nodes <= sketch_names


def test_prefix_bindings_are_published(query_dir):
    facts = bind_analysis.bind_report_to_graph(
        bind_analysis.analyse([str(query_dir / name) for name, _ in QUERIES]))

    bindings = {}
    for node in facts.subjects(rdflib.RDF.type, bind_analysis.TQ.PrefixBinding):
        prefix = facts.value(node, bind_analysis.TQ.prefix)
        namespace = facts.value(node, bind_analysis.TQ.namespace)
        source = facts.value(node, bind_analysis.TQ.source)
        bindings.setdefault(str(prefix), set()).add((str(source), str(namespace)))

    assert ("readings.rq", "https://example.org/other#") in bindings["ext"]
    # The empty prefix is a declaration like any other. Dropping it would
    # lose the commonest disagreement of all, since most queries declare one.
    assert {source for source, _ns in bindings[""]} == {"sites.rq", "readings.rq"}


def test_a_prefix_disagreement_is_visible_in_the_facts(tmp_path):
    """The reason the prefixes are published at all: two files, one prefix,
    two namespaces, and neither file wrong on its own."""
    (tmp_path / "a.rq").write_text(
        "prefix ext: <https://example.org/v1#>\nCONSTRUCT { ?s a ext:Thing . } WHERE { }",
        encoding="utf-8")
    (tmp_path / "b.rq").write_text(
        "prefix ext: <https://example.org/v2#>\nCONSTRUCT { ?s a ext:Thing . } WHERE { }",
        encoding="utf-8")

    facts = bind_analysis.bind_report_to_graph(
        bind_analysis.analyse([str(tmp_path / "a.rq"), str(tmp_path / "b.rq")]))
    namespaces = set(facts.query("""
        PREFIX tq: <https://semantechs.co.uk/ontology-quality/tarql/>
        SELECT ?namespace WHERE {
          ?b a tq:PrefixBinding ; tq:prefix "ext" ; tq:namespace ?namespace .
        }"""))
    assert len(namespaces) == 2
