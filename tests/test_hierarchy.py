"""Deep class hierarchies must not overflow the stack.

Four modules each carried their own recursive copy of the same hierarchy
walk, and all four guarded *cycles* but not *descent*. Depth follows the
longest ``rdfs:subClassOf`` chain in the input, one Python frame per link,
against CPython's default 1000-frame limit -- so a long chain raised
``RecursionError`` from what is only a metrics report. Memoisation hid it
whenever classes happened to be visited shallowest-first, which is why the
measured thresholds differed between modules running structurally identical
code (900 for ``ontologyeval``, 5000 for ``sketch``), and why declaring the
same chain in the other order was enough to trigger it.

The blank-node walks in ``reasoning/`` had the same shape for a different
reason: an RDF collection is a *chain* of blank nodes, one ``rdf:rest`` cell
per member, so depth there is set by the longest list in the input.

These are behavioural tests, not source greps: each builds an input past the
old ceiling and asserts the real answer comes back, so they fail loudly if
any of these walks is ever written recursively again.
"""
import ast
import pathlib
import random

import pytest
from rdflib import BNode, Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS

from ontology_suite import hierarchy
from ontology_suite.dataquality import data_quality
from ontology_suite.ontologyeval import ontology_evaluation
from ontology_suite.reasoning import profile
from ontology_suite.reasoning.sampling import sample_graph
from ontology_suite.sketch import ontology_quality
from ontology_suite.versioning import diff as versioning_diff

EX = "https://example.org/deep/"

# Comfortably past every measured threshold (900 was enough to break
# ontologyeval), while staying fast -- `ancestors` materialises a set per
# node, so its cost is quadratic in depth regardless of implementation.
DEEP = 2_000


def _subclass_chain(n: int) -> Graph:
    """C0 -> C1 -> ... -> Cn, every link declared, deepest link last."""
    graph = Graph()
    for i in range(n):
        child, parent = URIRef(f"{EX}C{i}"), URIRef(f"{EX}C{i + 1}")
        graph.add((child, RDF.type, OWL.Class))
        graph.add((parent, RDF.type, OWL.Class))
        graph.add((child, RDFS.subClassOf, parent))
    return graph


def _parents_map(n: int) -> dict:
    return {f"C{i}": [f"C{i + 1}"] for i in range(n)}


# ---------------------------------------------------------------------------
# The shared walks
# ---------------------------------------------------------------------------
def test_depth_reports_the_real_depth_of_a_very_deep_chain():
    """The reason a depth cap was not the fix: any cap low enough to be safe
    under a 1000-frame ceiling would silently truncate this to the cap."""
    assert hierarchy.depth("C0", _parents_map(100_000), {}) == 100_000


def test_ancestors_and_descendants_clear_the_old_ceiling():
    chain = _parents_map(DEEP)
    assert len(hierarchy.ancestors("C0", chain, {})) == DEEP

    children = {f"C{i + 1}": [f"C{i}"] for i in range(DEEP)}
    assert len(hierarchy.descendants_inclusive(f"C{DEEP}", children, {})) == DEEP + 1


def test_cycles_still_terminate():
    """A cycle contributed nothing to depth and closed the ancestor set
    before; unchanged. The point is that it terminates rather than that any
    particular number comes out."""
    cyclic = {"A": ["B"], "B": ["A"]}
    assert hierarchy.depth("A", cyclic, {}) == 2
    assert hierarchy.ancestors("A", cyclic, {}) == {"A", "B"}
    assert hierarchy.descendants_inclusive("A", cyclic, {}) == {"A", "B"}


def test_cyclic_nodes_are_still_reported():
    """`ontology_evaluation` surfaces this set as its `cyclic_classes` report
    flag, so losing it would silently drop a real finding."""
    seen = set()
    hierarchy.ancestors("A", {"A": ["B"], "B": ["A"]}, {}, cyclic=seen)
    assert seen == {"A"}


def _recursive_ancestors(cls, parents_of, memo, visiting=frozenset(), cyclic=None):
    if cls in memo:
        return memo[cls]
    if cls in visiting:
        if cyclic is not None:
            cyclic.add(cls)
        return set()
    result = set()
    for parent in parents_of.get(cls, ()):
        result.add(parent)
        result |= _recursive_ancestors(parent, parents_of, memo, visiting | {cls}, cyclic)
    memo[cls] = result
    return result


def _recursive_depth(cls, parents_of, memo, visiting=frozenset()):
    if cls in memo:
        return memo[cls]
    if cls in visiting:
        return 0
    parents = parents_of.get(cls, ())
    memo[cls] = 0 if not parents else 1 + max(
        _recursive_depth(p, parents_of, memo, visiting | {cls}) for p in parents
    )
    return memo[cls]


def _recursive_descendants(cls, children, memo):
    if cls in memo:
        return memo[cls]
    memo[cls] = {cls}
    result = {cls}
    for child in children.get(cls, ()):
        result |= _recursive_descendants(child, children, memo)
    memo[cls] = result
    return result


@pytest.mark.parametrize("cyclic_input", [False, True])
def test_iterative_walks_match_the_recursive_originals(cyclic_input):
    """Equivalence proof over random graphs in random visit orders -- results
    *and* memo contents, since callers share one memo across calls and the
    originals memoised results that a cycle had truncated."""
    for trial in range(60):
        rng = random.Random(trial)
        size = 40
        graph = {}
        for i in range(size):
            if cyclic_input:
                picks = [f"n{rng.randrange(size)}" for _ in range(rng.randint(0, 3))]
            elif i + 1 < size:
                picks = [f"n{rng.randrange(i + 1, size)}" for _ in range(rng.randint(0, 3))]
            else:
                picks = []
            if picks:
                graph[f"n{i}"] = sorted(set(picks))

        order = [f"n{i}" for i in range(size)]
        rng.shuffle(order)

        want_memo, got_memo, want_cyclic, got_cyclic = {}, {}, set(), set()
        for node in order:
            assert _recursive_ancestors(node, graph, want_memo, cyclic=want_cyclic) == \
                hierarchy.ancestors(node, graph, got_memo, cyclic=got_cyclic)
        assert want_memo == got_memo
        assert want_cyclic == got_cyclic

        want_memo, got_memo = {}, {}
        for node in order:
            assert _recursive_depth(node, graph, want_memo) == \
                hierarchy.depth(node, graph, got_memo)
        assert want_memo == got_memo

        want_memo, got_memo = {}, {}
        for node in order:
            assert _recursive_descendants(node, graph, want_memo) == \
                hierarchy.descendants_inclusive(node, graph, got_memo)
        assert want_memo == got_memo


# ---------------------------------------------------------------------------
# The callers, end to end -- each of these raised RecursionError
# ---------------------------------------------------------------------------
def test_ontology_evaluation_metrics_survive_a_deep_hierarchy():
    """Measured to raise RecursionError at depth 900 -- the shallowest of the
    four, because `compute_metrics` is already several frames deep by the
    time it starts descending."""
    graph = _subclass_chain(DEEP)
    metrics = ontology_evaluation.compute_metrics(ontology_evaluation.collect_schema(graph))
    assert max(m["dit"] for m in metrics["per_class"].values()) == DEEP


def test_sketch_metrics_survive_a_deep_hierarchy():
    graph = _subclass_chain(DEEP)
    metrics = ontology_quality.compute_metrics(ontology_quality.induce_schema(graph))
    assert max(m["dit"] for m in metrics["per_class"].values()) == DEEP


def test_data_quality_richness_survives_a_deep_hierarchy():
    graph = _subclass_chain(DEEP)
    data_quality.schema_richness(data_quality.ontology_declarations(graph), Graph())


def test_version_diff_subclass_expansion_survives_a_deep_hierarchy():
    children = {URIRef(f"{EX}C{i + 1}"): {URIRef(f"{EX}C{i}")} for i in range(DEEP)}
    expanded = versioning_diff._descendants_inclusive(URIRef(f"{EX}C{DEEP}"), children, {})
    assert len(expanded) == DEEP + 1


def test_cbd_survives_a_long_rdf_collection():
    """An RDF collection is a chain of blank nodes -- one `rdf:rest` cell per
    member -- so a single long `owl:oneOf`/`unionOf` is a deep walk, not a
    wide one. Measured RecursionError at 5,000 members."""
    graph = Graph()
    root = URIRef(f"{EX}Enum")
    graph.add((root, RDF.type, OWL.Class))
    head = BNode()
    Collection(graph, head, [URIRef(f"{EX}e{i}") for i in range(DEEP)])
    graph.add((root, OWL.oneOf, head))

    sampled = sample_graph(graph, 1)

    # Every list cell reached: rdf:first + rdf:rest per member, plus the
    # root's own rdf:type and owl:oneOf.
    assert len(sampled) == 2 * DEEP + 2


def test_cbd_matches_the_recursive_form_on_real_fixtures():
    """The CBD rewrite changed traversal order -- depth-first became
    frontier order -- so this proves the *graph* is unchanged, which is all
    either caller consumes. Checked over every subject of the repo's own
    fixtures rather than a synthetic chain, since the shapes that matter
    (restrictions, nested lists, shared blank nodes reachable two ways) are
    the ones real documents actually contain."""
    def recursive(graph, node, seen=None):
        seen = seen if seen is not None else set()
        if node in seen:
            return
        seen.add(node)
        for predicate, obj in graph.predicate_objects(node):
            yield (node, predicate, obj)
            if isinstance(obj, BNode):
                yield from recursive(graph, obj, seen)

    from ontology_suite import config
    from ontology_suite.reasoning.sampling import concise_bounded_description

    fixtures = [
        "examples/ontology/domain.ttl",
        "examples/checks_stress_test/stress-ontology.ttl",
        "examples/checks_stress_test/stress-data.ttl",
        "examples/property_axioms/ontology.ttl",
    ]
    checked = 0
    for relative in fixtures:
        graph = Graph().parse(config.REPO_ROOT / relative, format="turtle")
        for subject in sorted(set(graph.subjects()), key=str):
            want = set(recursive(graph, subject))
            got = set(concise_bounded_description(graph, subject))
            assert want == got, f"{relative}: CBD of {subject} differs"
            checked += 1
    assert checked > 200, f"only {checked} subjects checked -- fixtures may have shrunk"


def test_readable_subject_survives_a_long_blank_node_chain():
    graph = Graph()
    nodes = [BNode() for _ in range(DEEP)]
    named = URIRef(f"{EX}Named")
    graph.add((named, RDF.first, nodes[0]))
    for i in range(DEEP - 1):
        graph.add((nodes[i], RDF.first, nodes[i + 1]))

    assert profile._readable_subject(graph, nodes[-1]) == named


def test_the_recursion_limit_is_left_alone():
    """`sys.setrecursionlimit` is the tempting non-fix: it does not grow the
    C stack, so a limit high enough to cover a deep chain trades a catchable
    RecursionError for an uncatchable interpreter crash. If a future change
    reaches for it instead of iterating, this fails."""
    offenders = []
    for path in pathlib.Path("ontology_suite").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # An actual call, via the AST rather than a text search -- prose
            # naming it (hierarchy.py's own docstring explains why it is the
            # wrong fix) is not an offence.
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "setrecursionlimit":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"{offenders} raise the recursion limit instead of walking iteratively"
