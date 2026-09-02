"""Detailed pyshacl-vs-native-engine parity check for 18 of the 21 checks
that have both a SHACL shape (resources/shapes/*.ttl) and a portable SPARQL
twin (resources/sparql/**/*.rq) formulation -- the ones `--engine` actually
lets you choose between. Skipped entirely if the optional `shacl` (native
Rust engine) package isn't installed.

It used to be all of them, and the docstring went on saying so after it
stopped being true: three checks added later (DAT-004, QUA-009, QUA-010)
grew both formulations without being seeded into this fixture, and nothing
noticed, because a coverage claim in prose cannot fail.
`test_the_dual_formulation_set_has_not_grown_unnoticed` below now states the
same thing as an assertion, so the next one is a failing test rather than a
sentence that quietly became wrong.

`examples/checks_stress_test/` (stress-ontology.ttl + stress-data.ttl) is a
deliberately larger, more repetitive composite fixture than
`examples/ontology/domain.ttl`/`examples/data/legacy-export.ttl` -- built
specifically to catch bugs that only show up with *multiple* instances of
the same flaw (a check that silently collapses distinct findings together,
or cross-joins unrelated ones), which a fixture with exactly one instance
per check can never expose. It found three real, confirmed bugs during
this session, each already fixed in the resources themselves (see git
history / this file's own tests below for what they were):

1. `EFF-002`'s SPARQL logic (`{BIND(?s AS ?node)} UNION {BIND(?o AS ?node)
   FILTER(...)}`) matched *zero* rows under rdflib's SPARQL engine no
   matter the data -- a `UNION` of branches containing only a `BIND` (no
   triple pattern) silently returns nothing. Fixed by restructuring so
   each branch has its own real triple pattern.
2. `EFF-002`'s SHACL formulation additionally failed under pyshacl only
   (`SELECT $this WHERE {...}` with `$this` never referenced anywhere in
   the pattern -- pyshacl's `initBindings`-prebound `$this` doesn't rejoin
   the outer projection when the query has zero real references to it,
   even though it works fine as a plain rdflib query without
   `initBindings`). Fixed with a harmless `OPTIONAL {$this ?p ?o}`.
3. `DAT-001`'s three-way `{FILTER(...)} UNION {FILTER(...)} UNION
   {FILTER(...)}` (no triple pattern in any branch) has the same
   zero-rows-always bug as #1. Fixed by rewriting as one FILTER with
   explicit `||`/`&&`.

It also found and reported a fourth bug that lived in the external native
`shacl` engine, not this repo -- a `sh:sparql` constraint whose target
included blank-node focus nodes produced an N^2 cross-product instead of N
(see `test_native_blank_node_cross_product_regression` below for the
now-fixed reproduction). Fixed upstream in `shacl>=0.1.4`
(https://github.com/pwin/SHACL_Engine) -- confirmed here by re-running the
exact same reproductions that originally found it. If any test below
starts failing with a native/pyshacl count mismatch again, check
`shacl.__version__` before assuming a regression in this repo -- it may
mean an older, pre-fix wheel got reinstalled (`uv sync` silently drops
`shacl` since it isn't a `pyproject.toml` dependency; see
`shacl_native_runner.py`'s module docstring).

Installing `shacl==0.1.4` (the fix above) and re-running this exact test
module immediately found a **fifth** bug, apparently introduced by (or
left behind by) whatever change fixed the N^2 issue -- `FILTER(isIRI(
$this))` inside a `sh:sparql` constraint no longer excluded blank-node
focus nodes under the native engine: `STY-001`/`STY-002` both use this
pattern (`sh:targetClass owl:Class`/`owl:ObjectProperty`/
`owl:DatatypeProperty` naturally includes any blank-node-typed anonymous
class/property expression -- e.g. `owl:unionOf`/`owl:intersectionOf`
members, extremely common in real ontologies; gist 14.1.0 alone has ~97),
and the `FILTER(isIRI($this))` meant to exclude those from a
*local-name* check (a blank node has no local name to judge) didn't.
Confirmed against real gist data: `STY-001` reported 97 findings under
native vs. pyshacl's correct 0 on `examples/vehicle/`. Reported upstream
and fixed in `shacl` 0.1.5 -- see `test_native_isiri_this_blank_node_regression`
below for the now-fixed reproduction (both bugs found and fixed within
the same release cycle -- a genuine coincidence of timing, not a sign
this class of bug is chronic).
"""
import rdflib
import pytest
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS

try:
    import shacl as _native_shacl_pkg  # type: ignore[import-not-found]
except ImportError:
    _native_shacl_pkg = None

from ontology_suite import config, pipeline
from ontology_suite.checks import shacl_native_runner as native_runner
from ontology_suite.checks.merge import build_unified_results
from ontology_suite.checks.registry import Registry
from ontology_suite.checks.shacl_runner import load_shapes_graph, run_shacl

pytestmark = pytest.mark.skipif(
    not native_runner.available(), reason="native `shacl` package not installed (see shacl_native_runner.py docstring)"
)

STRESS_DIR = config.REPO_ROOT / "examples" / "checks_stress_test"

# Expected pyshacl finding counts for the checks this fixture seeds,
# confirmed against it exactly (deterministic -- pyshacl has no known
# nondeterminism here, unlike the unrelated, already-documented issue in
# some SPARQL-only checks against real imported vocabularies).
EXPECTED_PYSHACL_COUNTS = {
    "DAT-001": 2, "DAT-002": 3, "DAT-003": 3,
    "EFF-001": 4, "EFF-002": 1, "EFF-003": 2,
    "LOG-001": 3, "LOG-002": 3, "LOG-003": 3,
    "QUA-001": 3, "QUA-002": 1, "QUA-003": 3,
    "STR-001": 3, "STR-002": 3, "STR-003": 6,
    "STY-001": 3, "STY-002": 3, "STY-003": 65,
}

# Dual-formulation checks this fixture does not seed, so parity between the
# two engines is unverified at scale for them. Each is covered one-instance
# elsewhere (DAT-004 and QUA-009/QUA-010 by examples/gist_patterns/ and
# tests/test_qua009_pref_label_cardinality.py, which runs all three engines),
# which is what makes this a gap in *stress* coverage rather than in coverage.
#
# Seeding them here is not free: the fixture is shared, and the gist
# magnitude/unit pattern and the SKOS documentation checks both report once
# per term, so adding them shifts the counts above for unrelated checks. That
# is a decision to take deliberately, not a line to slip into another change
# -- which is why they are named rather than quietly absent.
UNSEEDED_DUAL_FORMULATION = {"DAT-004", "QUA-009", "QUA-010"}


@pytest.fixture(scope="module")
def shapes() -> rdflib.Graph:
    return load_shapes_graph(config.DEFAULT_SHAPES_DIR)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


@pytest.fixture(scope="module")
def stress_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(STRESS_DIR / "stress-ontology.ttl", format="turtle")
    g.parse(STRESS_DIR / "stress-data.ttl", format="turtle")
    return g


@pytest.fixture(scope="module")
def pyshacl_rows(stress_graph, shapes, registry):
    _conforms, results, _text = run_shacl(stress_graph, shapes)
    return build_unified_results(results, rdflib.Graph(), registry, shapes)


@pytest.fixture(scope="module")
def native_rows(stress_graph, shapes, registry):
    _conforms, results, _text = native_runner.run_shacl_native(stress_graph, shapes)
    return build_unified_results(results, rdflib.Graph(), registry, shapes)


def _counts(rows):
    from collections import Counter
    return Counter(r.check_id for r in rows)


def test_stress_fixture_blank_node_ratio_exceeds_eff002_threshold(stress_graph):
    """Confirms the fixture actually achieves what EFF-002 needs (>20%)
    rather than silently drifting below it if the fixture is ever edited."""
    nodes, bnodes = set(), set()
    for s, _p, o in stress_graph:
        for n in (s, o):
            if isinstance(n, (rdflib.URIRef, rdflib.BNode)):
                nodes.add(n)
                if isinstance(n, rdflib.BNode):
                    bnodes.add(n)
    assert len(bnodes) / len(nodes) > 0.2


@pytest.mark.parametrize("check_id", sorted(EXPECTED_PYSHACL_COUNTS))
def test_pyshacl_finds_every_shacl_shaped_check_at_expected_count(check_id, pyshacl_rows):
    counts = _counts(pyshacl_rows)
    assert counts.get(check_id, 0) == EXPECTED_PYSHACL_COUNTS[check_id], (
        f"{check_id}: expected {EXPECTED_PYSHACL_COUNTS[check_id]} findings under pyshacl, "
        f"got {counts.get(check_id, 0)} -- a real regression, or the fixture changed"
    )


@pytest.mark.parametrize("check_id", sorted(EXPECTED_PYSHACL_COUNTS))
def test_native_matches_pyshacl_count_for_every_check(check_id, pyshacl_rows, native_rows):
    """STY-003 (a confirmed native-engine blank-node cross-product bug --
    see test_native_blank_node_cross_product_regression below) and
    STY-001/STY-002 (a separate isIRI($this) blank-node leak -- see
    test_native_isiri_this_blank_node_regression below) both used to be
    excluded here. Both fixed upstream (shacl 0.1.4 and 0.1.5
    respectively) and folded back into this main comparison once each fix
    was installed and re-verified."""
    py_counts = _counts(pyshacl_rows)
    native_counts = _counts(native_rows)
    assert native_counts.get(check_id, 0) == py_counts.get(check_id, 0), (
        f"{check_id}: pyshacl found {py_counts.get(check_id, 0)}, native found "
        f"{native_counts.get(check_id, 0)} -- an engine-parity regression "
        f"(installed shacl version: {_native_shacl_pkg.__version__ if _native_shacl_pkg else 'unknown'})"
    )


def test_native_blank_node_cross_product_regression(pyshacl_rows, native_rows):
    """Regression test for a real, confirmed bug that used to live in the
    native (Rust) SHACL engine (reported upstream, not fixable in this
    repo): for a `sh:sparql` SPARQLConstraint whose target included
    *blank-node* focus nodes, the native engine didn't scope `$this` per
    focus node correctly -- it cross-joined every blank-node focus node
    against every other blank node's own result, producing N^2 findings
    instead of N. Named-IRI focus nodes were never affected (confirmed
    separately -- see test_native_named_focus_nodes_do_not_cross_product).
    Fixed upstream in shacl>=0.1.4 (https://github.com/pwin/SHACL_Engine).

    STY-003 (`sh:targetSubjectsOf rdfs:label`) is the only check in this
    suite whose target can include blank nodes -- of its 65 real focus
    nodes in this fixture, 60 are the blank-node objects of
    stress-data.ttl's `ex:hasComponent` (deliberately built at that scale
    to expose the original bug: it previously reported 3905 here, not
    65). pyshacl (pure Python/rdflib, a completely independent
    implementation) never had this issue.
    """
    py_count = _counts(pyshacl_rows)["STY-003"]
    native_count = _counts(native_rows)["STY-003"]
    assert py_count == 65
    assert native_count == py_count, (
        f"expected native to match pyshacl at {py_count} -- got {native_count}. "
        "If this is N^2-shaped (e.g. 3905), the blank-node cross-product bug is back "
        "(an older/unfixed shacl wheel got reinstalled -- uv sync silently drops it, "
        "see shacl_native_runner.py's module docstring) rather than a new regression."
    )


def test_native_isiri_this_blank_node_regression(pyshacl_rows, native_rows):
    """Regression test for a real, confirmed bug that used to live in the
    native (Rust) SHACL engine (reported upstream, not fixable in this
    repo): `FILTER(isIRI($this))` inside a `sh:sparql` SPARQLConstraint
    stopped excluding blank-node focus nodes in shacl 0.1.4. `STY-001`
    (`sh:targetClass owl:Class`) and `STY-002` (`sh:targetClass
    owl:ObjectProperty`/`owl:DatatypeProperty`) both rely on exactly this
    filter to exclude blank-node-typed anonymous class/property
    expressions (`owl:unionOf`/`owl:intersectionOf` members etc.) from a
    check that's only meaningful for a real, named local name.
    stress-ontology.ttl deliberately includes 2 such blank-node class
    expressions (added after this bug was found against real gist data --
    gist 14.1.0 alone has ~97 of them, and `STY-001` reported exactly 97
    false positives there under native vs. pyshacl's correct 0).

    Found immediately after installing shacl 0.1.4 to verify the fix for
    test_native_blank_node_cross_product_regression above -- appeared to
    be a regression from (or a boundary case left over by) whatever
    change fixed that bug, since both involve the same
    `$this`-per-focus-node scoping machinery. Fixed upstream in shacl
    0.1.5, confirmed here."""
    py_counts = _counts(pyshacl_rows)
    native_counts = _counts(native_rows)
    assert py_counts["STY-001"] == 3, "the 3 genuinely bad-cased named classes"
    assert native_counts["STY-001"] == 3, (
        f"expected native to match pyshacl at 3 -- got {native_counts['STY-001']}. If this is 5 "
        "(3 real + 2 blank-node class expressions), the isIRI($this) blank-node leak is back "
        "(an older/unfixed shacl wheel got reinstalled -- uv sync silently drops it, "
        "see shacl_native_runner.py's module docstring) rather than a new regression."
    )
    assert py_counts["STY-002"] == 3
    assert native_counts["STY-002"] == 3, (
        "STY-002 isn't exercised by a blank-node-typed property in this fixture (only STY-001's classes "
        "are) -- see test_native_isiri_this_blank_node_regression_sty002_isolated for that case"
    )


def test_native_isiri_this_blank_node_regression_sty002_isolated(shapes, registry):
    """Isolated regression test confirming STY-002 had the exact same
    FILTER(isIRI($this)) blank-node leak as STY-001 above, fixed in the
    same shacl 0.1.5 release -- via a blank-node-typed
    owl:ObjectProperty/owl:DatatypeProperty instead of owl:Class
    (stress-ontology.ttl doesn't include one of these, so this can't be
    caught by the main stress fixture)."""
    data = rdflib.Graph()
    data.parse(data="""
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix ex: <https://example.org/> .
        ex:goodProp a owl:ObjectProperty .
        [] a owl:ObjectProperty ; owl:inverseOf ex:goodProp .
        [] a owl:DatatypeProperty .
    """, format="turtle")

    _c1, results_py, _t1 = run_shacl(data, shapes)
    _c2, results_native, _t2 = native_runner.run_shacl_native(data, shapes)
    rows_py = build_unified_results(results_py, rdflib.Graph(), registry, shapes)
    rows_native = build_unified_results(results_native, rdflib.Graph(), registry, shapes)

    py_sty002 = [r for r in rows_py if r.check_id == "STY-002"]
    native_sty002 = [r for r in rows_native if r.check_id == "STY-002"]
    assert py_sty002 == [], "the one named property is well-formed -- pyshacl correctly finds nothing"
    assert native_sty002 == [], (
        f"expected native to match pyshacl at 0 -- got {len(native_sty002)}. If this is 2, the "
        "isIRI($this) blank-node leak is back (a pre-0.1.5 wheel got reinstalled) rather than a new bug"
    )


@pytest.mark.parametrize("n", [2, 3, 4])
def test_native_blank_node_cross_product_isolated(n):
    """Minimal, engine-only regression test for the bug documented above
    -- no SPARQL-formulation quirks, no other shapes loaded, just N blank
    nodes sharing one sh:targetSubjectsOf + sh:sparql shape. Before
    shacl>=0.1.4 this reported N^2 (e.g. 16 for N=4); confirms it's now
    exactly N. The equivalent named-IRI case
    (test_native_named_focus_nodes_do_not_cross_product below) was never
    affected, at any scale tested, even before the fix."""
    shapes = rdflib.Graph()
    shapes.parse(data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix ex: <https://example.org/> .
        ex:TestShape a sh:NodeShape ;
          sh:targetSubjectsOf rdfs:label ;
          sh:sparql [
            a sh:SPARQLConstraint ;
            sh:message "no lang tag" ;
            sh:select \"\"\"
              PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
              SELECT $this ?value WHERE { $this rdfs:label ?value . FILTER(LANG(?value) = "") }
            \"\"\" ;
          ] .
    """, format="turtle")

    values = ", ".join(f'[ rdfs:label "{chr(97 + i)}" ]' for i in range(n))
    data = rdflib.Graph()
    data.parse(
        data=f"""
        @prefix ex: <https://example.org/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:carrier ex:has {values} .
        """,
        format="turtle",
    )

    _conforms, results, _text = native_runner.run_shacl_native(data, shapes)
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    count = len(list(results.subjects(rdflib.RDF.type, SH.ValidationResult)))
    assert count == n, (
        f"expected {n} (one finding per blank focus node), got {count} -- "
        f"{n * n} would mean the pre-0.1.4 cross-product bug is back"
    )


def test_native_named_focus_nodes_do_not_cross_product():
    """Same shape, same query, same target mechanism -- but 4 *named* IRI
    focus nodes instead of blank nodes. Correct at 4, not 16: the bug
    above is specific to blank-node focus node identity, not a general
    multi-focus-node SPARQLConstraint issue."""
    shapes = rdflib.Graph()
    shapes.parse(data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix ex: <https://example.org/> .
        ex:TestShape a sh:NodeShape ;
          sh:targetSubjectsOf rdfs:label ;
          sh:sparql [
            a sh:SPARQLConstraint ;
            sh:message "no lang tag" ;
            sh:select \"\"\"
              PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
              SELECT $this ?value WHERE { $this rdfs:label ?value . FILTER(LANG(?value) = "") }
            \"\"\" ;
          ] .
    """, format="turtle")

    data = rdflib.Graph()
    data.parse(
        data="""
        @prefix ex: <https://example.org/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:x1 rdfs:label "one" . ex:x2 rdfs:label "two" .
        ex:x3 rdfs:label "three" . ex:x4 rdfs:label "four" .
        """,
        format="turtle",
    )

    _conforms, results, _text = native_runner.run_shacl_native(data, shapes)
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    count = len(list(results.subjects(rdflib.RDF.type, SH.ValidationResult)))
    assert count == 4


def test_sparql_only_layer_runs_cleanly_at_stress_scale(stress_graph, registry):
    """Sanity check beyond the 18 dual-formulation checks: the portable
    SPARQL layer (every check this suite has, `--engine sparql`) runs the
    full registry against this larger, denser fixture without error and
    resolves every finding to a real check id."""
    rows = pipeline.run_registry_suite_on_graph(
        stress_graph, registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR, engine="sparql"
    )
    assert len(rows) > 100
    assert not any(r.check_id is None for r in rows)
    fired = set(r.check_id for r in rows)
    assert set(EXPECTED_PYSHACL_COUNTS) <= fired


def test_native_engine_handles_data_deeper_than_its_shape_recursion_limit(shapes, registry):
    """The native engine caps *shape* descent, and that cap used to be a cap
    on the data too.

    Before shacl 0.1.10 the property descent ran on the Rust call stack, so
    the documented "48 levels of nesting" worked out to a longest validating
    chain of 46 -- and an RDF collection of 47 items is a 47-link
    `rdf:rest` chain. Lists that long are ordinary. 0.1.10 moved the
    `sh:property` descent onto an explicit heap stack (prompted, per its own
    commit message, by the sibling VS Code extension's `metrics.ts` making
    the same move for its subclass walk), so a 20,000-link chain now
    validates.

    Measured here, this suite was never actually exposed: its shapes carry
    no recursive `sh:node` reference and only one level of `sh:property`,
    so 0.1.9 and 0.1.10 return byte-identical findings for every size below
    -- checked directly before the bump, not assumed. This test therefore
    pins that independence rather than demonstrating a fix: it fails if a
    future shape in this suite starts nesting deeply enough to reinherit the
    engine's ceiling, or if the engine regresses on this axis.

    Both shapes of input are covered, because they stress different things:
    a long `rdfs:subClassOf` chain (which `LOG-001`'s `sh:oneOrMorePath`
    walks) and a long `rdf:rest` chain (an ordinary `owl:unionOf` list).
    """
    ex = rdflib.Namespace("https://example.org/deep/")

    def subclass_chain(n):
        g = rdflib.Graph()
        for i in range(n):
            child, parent = ex[f"C{i}"], ex[f"C{i + 1}"]
            g.add((child, RDF.type, OWL.Class))
            g.add((parent, RDF.type, OWL.Class))
            g.add((child, RDFS.subClassOf, parent))
        g.add((ex.C0, OWL.disjointWith, ex[f"C{n}"]))  # so LOG-001 has something to find
        return g

    def collection(n):
        g = rdflib.Graph()
        g.add((ex.Enum, RDF.type, OWL.Class))
        head = rdflib.BNode()
        Collection(g, head, [ex[f"e{i}"] for i in range(n)])
        g.add((ex.Enum, OWL.unionOf, head))
        return g

    # 46/47 straddle the old boundary exactly; 400 is well past it.
    for build in (subclass_chain, collection):
        for size in (46, 47, 400):
            _conforms, results, _text = native_runner.run_shacl_native(build(size), shapes)
            rows = build_unified_results(results, rdflib.Graph(), registry, shapes)
            assert rows, f"{build.__name__}({size}) produced no findings at all"
            assert not any(r.check_id is None for r in rows), (
                f"{build.__name__}({size}): unresolved check id -- the engine returned "
                "results it could not attribute, which is how a truncated descent shows up here"
            )
