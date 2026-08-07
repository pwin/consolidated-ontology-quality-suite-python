"""Regression tests against examples/vehicle/ (a real vehicle ontology
importing gist 14.1.0 by its owl:versionIRI, with a local copy of gist
alongside it) -- added specifically to catch a class of bug that only
shows up against a real, large, professionally-versioned ontology, never
against small hand-written fixtures. Running the full registry suite
against this pair caught nine separate real bugs in one session:

1. Import resolution only matched an `owl:imports` IRI against a candidate
   file's own `a owl:Ontology` identity, not its `owl:versionIRI` -- but
   `owl:imports` commonly targets a specific *version* IRI (exactly what
   gist itself does), so the import silently never resolved even with the
   exact file sitting right there locally. Every gist term used then
   flooded out as a false "undefined class/property".
2. STR-001's built-in-vocabulary exclusion list was missing owl:Restriction
   (among others), so every anonymous restriction blank node -- which
   every non-trivial ontology has many of -- flooded out as a false
   "undefined class".
3. DAT-002's "dangling reference" heuristic didn't recognize an ontology's
   own owl:versionIRI (routinely the target of rdfs:isDefinedBy on every
   term it declares) or rdfs:seeAlso targets as legitimate references.
4. registry.py's OQ namespace constant didn't match the actual `oq:`
   prefix used throughout shapes/*.ttl and sparql/**/*.rq, so the
   oq:checkId lookup silently matched nothing -- every SHACL-sourced
   finding from a nested `sh:property [...]` shape came back with no
   check id, category, or remediation text at all.
5. QUA-001's native SHACL shape only checked rdfs:label, silently
   diverging from its own SPARQL twin's `rdfs:label|skos:prefLabel`
   -- flooding on any ontology (gist included) documented with SKOS labels.
6. `run`'s own `checks`/`sketch`/`data` stages silently dropped
   `--import-dir`/`--exclude-imports`/`--allow-network` even when passed on
   the command line -- only the separate `ontology` stage (and
   `version-diff`) actually resolved imports; `pipeline.run_checks_stage`
   et al. just did a plain, non-resolving parse. A real user ran
   `ontology-suite run --ontology ... --import-dir ... --allow-network`
   and still got the full false-positive flood from bug #1, because the
   flags never reached the `checks` stage that produced most of it. Fixed
   by a shared `pipeline.load_ontology_graph` helper every stage now uses.
7. The reasoning layer reran *all* of `sparql/logical/*.rq` against the
   owlrl-materialized closure, but not every "logical cogency" check
   describes a genuine contradiction -- some describe something about how
   the ontology's own axioms were *authored*, and become falsely true (or
   newly, spuriously true) once inference has already happened:
   `LOG-003` (redundant equivalentClass+subClassOf) is vacuously true for
   *every* equivalentClass axiom post-closure (OWL2 RL entails the
   reciprocal subClassOf unconditionally) -- 164 false findings, all 59 of
   gist's equivalentClass axioms, none authored redundantly. `LOG-006`/
   `LOG-007` (symmetric/transitive property with unequal domain/range)
   check the property's own *directly declared* domain/range, but RDFS
   entails domain/range onto a property via `rdfs:subPropertyOf` -- so a
   property with no domain/range of its own can inherit a mismatched pair
   from its superproperty post-closure (`LOG-007`: 0 -> 12 false findings).
   Fixed by only rerunning the genuinely closure-safe subset
   (`sparql/logical/closure-safe/`: LOG-001/002/004/005) against the
   closure -- see that directory's own README.md for the full reasoning.
8. A systematic pass through every remaining category of finding (prompted
   by the user asking "how does gist:isDirectPartOf have two inverses?")
   found three more, unrelated to imports/closure:
   - `LOG-004` (property has more than one declared inverse) was **100%
     false positives** on this graph: gist writes inline anonymous inverse
     expressions (`owl:onProperty [ owl:inverseOf P ]`) rather than naming
     a separate inverse property, and every such use mints a fresh blank
     node even though they're all semantically the same relation. Fixed by
     requiring both sides of the "two distinct inverses" comparison to be
     named (`isIRI`), not blank nodes.
   - `STR-004` (class has no formal definition) was missing the
     `http://www.w3.org/` exemption every other "must have local X" check
     already has, so it flagged `skos:Concept` for lacking a definition
     that legitimately lives in the SKOS spec, not this graph.
   - `QUA-004` (resource missing skos:prefLabel) required `skos:prefLabel`
     specifically, unlike `QUA-001`/`QUA-002`'s established
     `rdfs:label|skos:prefLabel` alternation -- flagging the vehicle
     ontology's own IRI despite it having a perfectly good `rdfs:label`.
     It also didn't exempt `owl:versionIRI` self-references or
     `rdfs:seeAlso` targets (both already exempted in `DAT-002`), flagging
     gist's own versionIRI and an IANA media-types registry URL for
     "missing a label."
   Everything else checked in this pass -- `STR-002`, `STR-003`, `STR-007`,
   `QUA-001` -- was confirmed genuine, real signal. `STY-004`'s
   9 findings are also real (the labels genuinely don't algorithmically
   match their local names) but mostly reflect gist's deliberate
   abbreviation/acronym convention (`GeoPoint` labeled "Geographic Point",
   `AdasSystem` labeled "ADAS") rather than actual naming drift -- left
   as-is; this is a judgment call for a human reviewer, not a check bug.
9. Comparing `--engine sparql` against `--engine both` on this same graph
   surfaced one more, one-finding discrepancy: `QUA-002`'s native SHACL
   shape checked `rdfs:label` only, silently diverging from its own
   SPARQL twin's `rdfs:label|skos:prefLabel` alternation (the same class
   of bug as `QUA-001`'s, bug #5 above, just not caught the first time
   because gist's own ontology header -- `https://w3id.org/semanticarts/
   ontology/gistCore`, which carries `skos:prefLabel "gist"` but no
   `rdfs:label` -- happens to be the one place this specific check looks
   at the *ontology* resource rather than classes/properties). Fixed by
   rewriting the SHACL shape to `sh:sparql` accepting either predicate,
   matching QUA-001's own fix.

This test is comparatively slow (pyshacl over a real ~3,300-triple merged
graph takes on the order of minutes, not the sub-second unit tests
elsewhere in this suite) -- that's expected, not a regression.
"""
import pytest
from rdflib import Graph

from ontology_suite import config, pipeline
from ontology_suite.checks.registry import Registry
from ontology_suite.ontologyeval import ontology_evaluation as oe
from ontology_suite.reasoning import consistency

VEHICLE_DIR = config.REPO_ROOT / "examples" / "vehicle"
VEHICLE_ONTOLOGY = VEHICLE_DIR / "vehicle-ontology.ttl"

pytestmark = pytest.mark.skipif(not VEHICLE_ONTOLOGY.is_file(), reason="examples/vehicle/ not present")


@pytest.fixture(scope="module")
def merged_graph():
    graph, report = oe.resolve_imports(str(VEHICLE_ONTOLOGY), str(VEHICLE_DIR), False, oe.DEFAULT_IMPORT_GLOBS)
    return graph, report


@pytest.fixture(scope="module")
def registry_rows(merged_graph):
    graph, _report = merged_graph
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    rows = pipeline.run_registry_suite_on_graph(graph, registry, config.DEFAULT_SHAPES_DIR, config.DEFAULT_SPARQL_DIR)
    return rows


def test_import_resolves_gist_by_its_version_iri(merged_graph):
    _graph, report = merged_graph
    assert report["unresolved"] == []
    assert any("gistCore14.1.0" in r["iri"] for r in report["resolved"])


def test_merged_graph_actually_contains_gist(merged_graph):
    graph, _report = merged_graph
    # gist 14.1.0 alone declares ~96 classes; if the import silently failed
    # to resolve, this graph would only have the vehicle ontology's own
    # handful of terms.
    assert len(graph) > 2000


def test_no_unmapped_check_ids(registry_rows):
    unmapped = [r for r in registry_rows if r.check_id is None]
    assert unmapped == [], f"{len(unmapped)} findings resolved to no check id at all"


def test_total_finding_count_is_small_and_accurate(registry_rows):
    """Before the fixes in this file's module docstring, this same graph
    produced 894 findings -- almost entirely false positives from nine
    stacked bugs (import resolution, blank-node/vocabulary exclusion gaps,
    a namespace mismatch, missing rdfs:label/skos:prefLabel alternation,
    dropped CLI flags, and QUA-002's SHACL shape checking rdfs:label only
    where its SPARQL twin accepted rdfs:label|skos:prefLabel -- caught via
    --engine sparql vs --engine both disagreeing by one finding against
    gist's own ontology IRI, which carries skos:prefLabel but no
    rdfs:label). The real signal, once every one of those is fixed, is
    exactly 55: STR-003 (35, genuinely unconstrained gist properties, by
    design), STY-004 (9, real but mostly gist's deliberate abbreviation
    convention), QUA-004 (4), STR-002/STR-007/STR-004 (2 each), QUA-001
    (1). A regression back toward hundreds of findings means one of the
    fixed bugs came back."""
    assert len(registry_rows) == 55, (
        f"expected exactly 55 verified-genuine findings, got {len(registry_rows)} -- "
        "one of the false-positive-flood bugs this test module exists to catch may have regressed"
    )


def test_no_false_positive_undefined_class_for_owl_restriction(registry_rows):
    assert [r for r in registry_rows if r.check_id == "STR-001"] == []


def test_dat002_does_not_flag_ontology_version_iris(registry_rows):
    dat002 = [r for r in registry_rows if r.check_id == "DAT-002"]
    assert not any("gistCore14.1.0" in (r.value or "") for r in dat002)
    assert not any("example.org/ontology/vehicle/" in (r.value or "") for r in dat002)


def test_qua001_recognizes_skos_preflabel(registry_rows):
    """gist documents its terms with skos:prefLabel, not rdfs:label -- none
    of those should be flagged as missing a label. The one legitimate
    finding is skos:Concept itself: external W3C vocabulary used as a
    class, with no local label -- exactly the same class of thing STR-001/
    STR-002's `http://www.w3.org/` exemption already treats as normal."""
    qua001 = [r for r in registry_rows if r.check_id == "QUA-001"]
    assert len(qua001) == 1, (
        f"expected exactly the one legitimate skos:Concept finding, got {len(qua001)} -- "
        "the skos:prefLabel|rdfs:label alternation or the isIRI blank-node guard may have regressed"
    )
    assert qua001[0].focus_node == "http://www.w3.org/2004/02/skos/core#Concept"


def test_str003_recognizes_gist_domain_range_includes(registry_rows):
    str003 = [r for r in registry_rows if r.check_id == "STR-003"]
    assert len(str003) == 35, (
        f"expected only genuinely unconstrained properties, got {len(str003)} -- "
        "the gist:domainIncludes/rangeIncludes recognition may have regressed"
    )


def test_checks_stage_forwards_import_args_to_load_ontology_graph(monkeypatch, tmp_path):
    """Bug #6 was specifically that `cli.py`'s `run` command never forwarded
    --import-dir/--exclude-imports/--allow-network into
    pipeline.run_checks_stage -- not that ontology_evaluation.resolve_imports
    itself was broken (that's already covered, cheaply, by
    test_import_resolves_gist_by_its_version_iri and
    test_no_false_positive_undefined_class_for_owl_restriction, both via the
    shared `registry_rows`/`merged_graph` fixtures).

    This used to re-run pipeline.run_checks_stage against the real vehicle+
    gist fixture to prove the same thing end to end -- correct, but it paid
    the full ~220s pyshacl+SPARQL registry-suite cost a *second* time in the
    same test session just to observe that three keyword arguments reached
    their destination. Spying on `pipeline.load_ontology_graph` directly
    tests exactly that plumbing, in well under a second, with no loss of
    real coverage: the "does import resolution actually work" and "is
    STR-001 correctly empty" claims remain fully covered elsewhere.
    """
    captured = {}

    def fake_load_ontology_graph(ontology_path, *, import_dir=None, exclude_imports=False, allow_network=False):
        captured["ontology_path"] = ontology_path
        captured["import_dir"] = import_dir
        captured["exclude_imports"] = exclude_imports
        captured["allow_network"] = allow_network
        return Graph()

    monkeypatch.setattr(pipeline, "load_ontology_graph", fake_load_ontology_graph)

    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    pipeline.run_checks_stage(
        registry, tmp_path, ontology_path=VEHICLE_ONTOLOGY,
        import_dir=VEHICLE_DIR, exclude_imports=True, allow_network=True,
    )

    assert captured == {
        "ontology_path": VEHICLE_ONTOLOGY,
        "import_dir": VEHICLE_DIR,
        "exclude_imports": True,
        "allow_network": True,
    }


def test_reasoning_layer_does_not_flood_log003_post_closure(merged_graph):
    """LOG-003 (redundant equivalentClass+subClassOf) is vacuously true for
    every equivalentClass axiom once the owlrl closure entails the
    reciprocal subClassOf -- rerunning it post-closure should be excluded
    (sparql/logical/closure-safe/ vs plain sparql/logical/), not produce a
    finding for every one of gist's 59 equivalentClass axioms."""
    graph, _report = merged_graph
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    rows = consistency.run_consistency_checks(graph, registry, config.DEFAULT_SPARQL_DIR, reasoner="owlrl-only")
    log003 = [r for r in rows if r.check_id == "LOG-003"]
    log007 = [r for r in rows if r.check_id == "LOG-007"]
    assert log003 == [], f"LOG-003 should not be rerun post-closure at all, got {len(log003)} findings"
    assert log007 == [], f"LOG-007 should not be rerun post-closure at all, got {len(log007)} findings"


def test_log004_ignores_inline_anonymous_inverse_expressions(registry_rows):
    """gist writes `owl:onProperty [ owl:inverseOf P ]` (an inline anonymous
    inverse-property expression) instead of naming a separate inverse
    property -- every use mints a fresh blank node, even though all of them
    denote the exact same relation. LOG-004 must not count those as
    distinct declared inverses: on this graph, 100% of its raw findings
    (54) were exactly this blank-node artifact, across three properties
    that have zero genuinely distinct *named* inverses between them."""
    assert [r for r in registry_rows if r.check_id == "LOG-004"] == []


def test_str004_exempts_external_w3c_vocabulary(registry_rows):
    """skos:Concept is defined by the SKOS spec itself, not this graph --
    same http://www.w3.org/ exemption STR-001/STR-002/DAT-002/QUA-004/
    STY-004 already apply."""
    str004 = [r for r in registry_rows if r.check_id == "STR-004"]
    assert not any(r.focus_node.startswith("http://www.w3.org/") for r in str004)


def test_qua004_accepts_rdfs_label_and_exempts_version_iris(registry_rows):
    """The vehicle ontology's own IRI has a perfectly good rdfs:label
    ("Vehicle Domain Ontology"@en) -- QUA-004 must accept that as
    equally valid to skos:prefLabel, matching QUA-001/QUA-002's own
    established alternation, rather than flagging it as unlabeled. It must
    also not flag gist's or the vehicle ontology's own owl:versionIRI, or
    the IANA media-types URL referenced via rdfs:seeAlso -- none of those
    are "real" resources this ontology owns and should label itself."""
    qua004 = [r for r in registry_rows if r.check_id == "QUA-004"]
    flagged = {r.focus_node for r in qua004}
    assert "https://example.org/ontology/vehicle" not in flagged
    assert not any("gistCore14.1.0" in f or "example.org/ontology/vehicle/" in f for f in flagged)
    assert not any("iana.org" in f for f in flagged)
    # The remaining findings should be exactly the genuine ones: two
    # undeclared vann: properties (vann was never imported) and two
    # example gist:Magnitude individuals with no label at all.
    assert len(qua004) == 4, f"expected exactly 4 genuine findings, got {len(qua004)}: {sorted(flagged)}"
