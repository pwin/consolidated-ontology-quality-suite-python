"""`--inference rdfs` applies to the graph both formulations read.

It used to be a parameter of the SHACL arm's `validate` call. The portable
SPARQL arm never makes that call, so the closure reached half the suite: the
two arms checked different graphs, and their findings were then merged under
one dedup key as though they were one run. Under `--engine sparql` -- the
combination this repo's own walkthrough recommends for CI -- the flag did
nothing whatever and said nothing about it.

Measured on `examples/acme_robotics/acme-org-v1.ttl` before the fix:

    engine   none   rdfs
    sparql    479    479      <- ignored outright
    both      479    497      <- one arm only

and after it, 489 for both engines: the number the two formulations agree on.
The old 497 was not eighteen extra true findings. It was one arm's
entailment findings plus the other arm's unresolved ones -- a count for a
graph that never existed.

The closure comes from the SHACL engine itself (`shacl.rdfs_closure`, added
for this in engine 0.3.3), not from owlrl and not from a few rules written
here. A second closure would disagree with the engine's in exactly the way
two graphs did, one level further up and harder to see. owlrl's would be
wrong on its own terms as well: it materialises the axiomatic rules, typing
every term `rdfs:Resource`, which no shape acts on.
"""
import pytest
from rdflib import Graph, URIRef

from ontology_suite import config, pipeline
from ontology_suite.checks.registry import Registry
from ontology_suite.checks.shacl_native_runner import (
    available as native_shacl_available,
    rdfs_closure,
)

pytestmark = pytest.mark.skipif(
    not native_shacl_available(), reason="the `shacl` engine package is not installed"
)

EX = "https://example.org/inference/"

# rdfs9 types the instance through the subclass; rdfs7 promotes the
# sub-property use. The second is the one SHACL will not resolve by itself,
# which is why materialising exists at all.
SOURCE = f"""
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <{EX}> .
ex: a owl:Ontology .
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:parent a owl:ObjectProperty .
ex:father a owl:ObjectProperty ; rdfs:subPropertyOf ex:parent .
ex:rex a owl:NamedIndividual, ex:Dog .
ex:rex ex:father ex:fido .
"""


def _graph(text: str = SOURCE) -> Graph:
    graph = Graph()
    graph.parse(data=text, format="turtle")
    return graph


# ---------------------------------------------------------------------------
# the closure itself
# ---------------------------------------------------------------------------
def test_the_closure_entails_through_subclass_and_subproperty():
    closed = rdfs_closure(_graph())
    assert (URIRef(EX + "rex"), None, URIRef(EX + "Animal")) in closed, "rdfs9 did not fire"
    assert (URIRef(EX + "rex"), URIRef(EX + "parent"), URIRef(EX + "fido")) in closed, (
        "rdfs7 did not fire -- the entailment SHACL cannot resolve on its own"
    )


def test_the_closure_adds_rather_than_replaces():
    graph = _graph()
    closed = rdfs_closure(graph)
    assert len(closed) > len(graph)
    for triple in graph:
        assert triple in closed, "the closure dropped an asserted triple"


def test_the_closure_leaves_out_the_axiomatic_rules():
    """The reason owlrl's closure is the wrong tool here. Typing every term
    `rdfs:Resource` is noise no shape acts on, and it multiplies a small
    document several times over -- two triples became twelve when measured."""
    closed = rdfs_closure(_graph("@prefix ex: <" + EX + "> .\nex:a ex:p ex:b .\n"))
    resource = URIRef("http://www.w3.org/2000/01/rdf-schema#Resource")
    assert not list(closed.triples((None, None, resource)))
    assert len(closed) == 1, f"nothing should be entailed from one triple, got {len(closed)}"


def test_an_entailment_rdf_cannot_express_is_dropped_not_raised():
    """`rdfs3` types the *object* of a triple, so a property with a declared
    range and a literal value entails a literal-subject triple. RDFS says it
    is entailed and RDF has no syntax for it.

    The engine's internal graph holds such generalised triples happily, which
    is why validating with `inference="rdfs"` never hit this -- and why
    serialising the same closure did, on the first real ontology tried.
    """
    closed = rdfs_closure(_graph(
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        f"@prefix ex: <{EX}> .\n"
        "ex:mbox rdfs:range ex:Address .\n"
        'ex:dave ex:mbox "dave@example.com" .\n'
    ))
    assert len(closed) >= 2, "the asserted triples must survive"


# ---------------------------------------------------------------------------
# both arms read the same graph
# ---------------------------------------------------------------------------
def _findings(engine: str, inference: str):
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    rows = pipeline.run_registry_suite_on_graph(
        _graph(), registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR,
        inference, engine=engine,
    )
    return {(r.check_id, r.focus_node, r.path, r.value) for r in rows}


def test_the_closure_changes_what_the_sparql_arm_sees():
    """The silent case, and the one recommended for CI. Before the fix these
    two were identical, because the flag never reached this arm."""
    assert _findings("sparql", "rdfs") != _findings("sparql", "none")


def test_the_two_engines_agree_once_they_read_the_same_graph():
    """`both` runs the shapes and the portable queries and cross-checks them.
    That comparison is only meaningful if they were asked about one graph."""
    assert _findings("both", "rdfs") >= _findings("sparql", "rdfs"), (
        "the SHACL arm should add findings, never contradict the SPARQL arm's"
    )
    sparql_only = _findings("sparql", "rdfs") - _findings("both", "rdfs")
    assert sparql_only == set(), (
        f"the portable arm found what the combined run did not: {sorted(sparql_only)}"
    )


# ---------------------------------------------------------------------------
# what is refused, and no longer ignored
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["sparql", "both", "shacl"])
@pytest.mark.parametrize("inference", ["owlrl", "both"])
def test_an_unavailable_inference_mode_is_refused_under_every_engine(engine, inference):
    """The check sat inside the `uses_shacl` branch, so `--engine sparql
    --inference owlrl` was accepted and quietly did nothing."""
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    with pytest.raises(ValueError, match="is not available"):
        pipeline.run_registry_suite_on_graph(
            _graph(), registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR,
            inference, engine=engine,
        )


def test_the_cli_no_longer_offers_a_mode_that_cannot_run():
    """`owlrl`/`both` raised whatever was passed, so offering them in --help
    advertised something the suite would refuse."""
    from ontology_suite import cli

    parser = cli.build_arg_parser()
    args = parser.parse_args(["checks", "--ontology", "x.ttl", "--inference", "rdfs"])
    assert args.inference == "rdfs"
    with pytest.raises(SystemExit):
        parser.parse_args(["checks", "--ontology", "x.ttl", "--inference", "owlrl"])


def test_the_stage_publishes_the_graph_its_findings_came_from(tmp_path):
    """`artifacts["graph"]` used to be the *unclosed* graph while `rows`
    described the closed one.

    `run_registry_suite_on_graph` closes the graph it is given by rebinding its
    own local, so the caller's graph stays as it was. Nothing reads that
    artefact today, which is the only reason it was not a live bug -- and
    exactly why it would have been a puzzling one later.
    """
    source = tmp_path / "o.ttl"
    source.write_text(SOURCE, encoding="utf-8")
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))

    stage = pipeline.run_checks_stage(
        registry, tmp_path / "out", ontology_path=str(source),
        inference="rdfs", engine="sparql",
    )
    published = stage.artifacts["graph"]
    assert (URIRef(EX + "rex"), None, URIRef(EX + "Animal")) in published, (
        "the published graph is missing the entailments the findings were based on"
    )


def test_closing_for_none_hands_back_the_same_object():
    """The ordinary path must not pay for a serialise/parse round trip, which
    is what `--inference rdfs` costs on a large graph."""
    graph = _graph()
    assert pipeline.rdfs_closure_for(graph, "none") is graph


def test_blank_node_identities_are_not_preserved_by_the_closure():
    """Recorded rather than fixed. The closure round-trips through Turtle, so
    blank nodes come back relabelled: a finding about one under
    `--inference rdfs` will not name the label the source file used.

    `checks/locate.py` already declines to place blank nodes, so nothing points
    at a wrong line because of it. Pinned so the behaviour is a known cost
    rather than a surprise, and so a future fix has something to flip.
    """
    from rdflib import BNode

    graph = Graph()
    node = BNode("keepme")
    graph.add((node, URIRef(EX + "p"), URIRef(EX + "o")))
    closed = pipeline.rdfs_closure_for(graph, "rdfs")

    labels = {str(s) for s in closed.subjects() if isinstance(s, BNode)}
    assert labels, "the blank node should still be there in some form"
    assert "keepme" not in labels, (
        "if labels now survive, this cost is gone -- update the docstring on "
        "pipeline.rdfs_closure_for and delete this test"
    )
