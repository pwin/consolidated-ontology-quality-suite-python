"""A SHACL `sh:message` is a template, and both engines returned it raw.

SHACL 1.0 section 6.2 makes `sh:message` a template: `{$this}`, `{$value}`
and `{$path}` are substituted from the constraint's own bindings. The
placeholder is invariably the part of the sentence that says *which* term the
finding is about, so a message that arrives unsubstituted is a finding nobody
can act on -- "{$this} is disjoint with one of its own transitive
superclasses" names no class.

Measured over the two ontology fixtures below, 84 SHACL rows:

    engine     rows   unsubstituted
    sparql      110       0          (CONCAT-built, never affected)
    shacl (pyshacl) 84    2          LOG-001, LOG-003
    native (Rust)   84   25          across ten check ids

The native engine is the default when installed, so the worse of the two was
what most runs got. It went unnoticed because the two formulations of a check
disagreed only about the *prose*: `merge.py`'s dedup key is
`(check_id, focus_node, path, value)`, so the rows still merged, and a
`--engine both` run showed one row per finding carrying whichever message
happened to survive.

Found in the VS Code extension first, by its own dual-formulation fixture,
and confirmed here rather than assumed -- the two projects share these
shapes, so a defect in the shapes' prose contract is a defect in both.

The strongest assertion here is the last one: the two engines must produce
the *same* message for the same finding. Counting placeholders proves only
that the text changed; agreeing proves the substitution put the right term in.
"""
import pytest
import rdflib

from ontology_suite import config, pipeline
from ontology_suite.checks.merge import substitute_message_placeholders
from ontology_suite.checks.registry import Registry
from ontology_suite.checks.shacl_native_runner import available as native_available

PLACEHOLDERS = ("{$this}", "{$value}", "{$path}", "{?this}", "{?value}", "{?path}")

ENGINES = ["sparql", "shacl", "both"] + (["native", "native+sparql"] if native_available() else [])


@pytest.fixture(scope="module")
def graph():
    g = rdflib.Graph()
    for name in ("examples/ontology/domain.ttl", "examples/property_axioms/ontology.ttl"):
        g.parse(config.REPO_ROOT / name, format="turtle")
    return g


@pytest.fixture(scope="module")
def registry():
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


@pytest.mark.parametrize("engine", ENGINES)
def test_no_finding_reaches_a_reader_with_a_placeholder(graph, registry, engine):
    rows = pipeline.run_registry_suite_on_graph(graph, registry, engine=engine)
    assert rows, "fixture produced no findings at all -- the assertion below would be vacuous"
    unsubstituted = [
        (r.check_id, r.message) for r in rows
        if r.message and any(token in r.message for token in PLACEHOLDERS)
    ]
    assert unsubstituted == [], f"{engine} left {len(unsubstituted)} message(s) unsubstituted"


@pytest.mark.skipif(not native_available(), reason="native `shacl` engine not installed")
def test_both_shacl_engines_word_the_same_finding_the_same_way(graph, registry):
    """Placeholder counts can be driven to zero by substituting the wrong
    thing. Agreement cannot: pyshacl does its own substitution, so matching it
    term for term is what says the right binding went in."""
    def keyed(engine):
        return {
            (r.check_id, r.focus_node, r.path or "", r.value or ""): r.message
            for r in pipeline.run_registry_suite_on_graph(graph, registry, engine=engine)
        }

    pyshacl, native = keyed("shacl"), keyed("native")
    shared = set(pyshacl) & set(native)
    assert len(shared) == len(pyshacl) == len(native), "the engines no longer report the same findings"
    differing = {k: (pyshacl[k], native[k]) for k in shared if pyshacl[k] != native[k]}
    assert differing == {}, f"{len(differing)} finding(s) are worded differently by the two engines"


def test_the_term_that_replaces_the_placeholder_is_the_right_one(graph, registry):
    """`{?value}` must be the *value*, not the focus node.

    `merge.py` defaults an unbound `sh:value` to the focus node, so a naive
    substitution renders DAT-002 as "ex:worksAt references an IRI
    (ex:worksAt)" -- grammatical, and wrong about the only thing the sentence
    is for.
    """
    rows = {r.check_id: r for r in pipeline.run_registry_suite_on_graph(graph, registry, engine="shacl")}
    dat002 = rows["DAT-002"]
    assert dat002.value in dat002.message
    assert dat002.value != dat002.focus_node, "fixture no longer exercises a distinct value"
    assert dat002.focus_node in dat002.message


# ---------------------------------------------------------------------------
# The substitution itself
# ---------------------------------------------------------------------------
def test_only_the_bindings_a_result_carries_are_substituted():
    """A constraint parameter is left visible rather than replaced with
    "None". The value is genuinely absent from the result, and a placeholder
    that says so beats a sentence that asserts something false."""
    out = substitute_message_placeholders(
        "{$this} has more than {$maxCount} values for {$path}", "ex:A", "ex:p", None
    )
    assert out == "ex:A has more than {$maxCount} values for ex:p"


def test_an_absent_binding_leaves_its_own_placeholder_alone():
    out = substitute_message_placeholders("{$this} at {$path}", "ex:A", None, None)
    assert out == "ex:A at {$path}"


def test_both_spellings_are_recognised():
    """SHACL writes `{$this}`; this suite's own shapes use `{?value}` in
    DAT-002. Both are in the wild and both have to work."""
    assert substitute_message_placeholders("{?this} {$this}", "ex:A", None, None) == "ex:A ex:A"
    assert substitute_message_placeholders("{?value}", None, None, "v") == "v"


def test_a_message_with_no_placeholder_is_returned_unchanged():
    assert substitute_message_placeholders("plain text", "ex:A", "ex:p", "v") == "plain text"
    assert substitute_message_placeholders(None, "ex:A", None, None) == ""
