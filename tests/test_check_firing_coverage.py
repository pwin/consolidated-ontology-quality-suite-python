"""Which registry checks have ever been *observed firing* -- as opposed to
merely being implemented.

`test_check_coverage.py` asserts every registry id has an implementation
somewhere. That is a weaker property than it sounds, because a check whose
query is subtly wrong doesn't error -- it quietly matches nothing and looks
exactly like a clean graph. This suite has shipped that bug four separate
times, each found only by running against real data long after the check was
written and reviewed:

* `REA-001` assumed `owl:disjointWith` gets symmetrized by reasoning, so its
  `FILTER(STR(?c1) < STR(?c2))` matched in one direction only;
* `DAT-001` used `{FILTER(...)} UNION {FILTER(...)}` with no triple pattern
  in any branch, which rdflib evaluates to zero rows regardless of the data;
* `EFF-002` never rejoined pyshacl's prebound `$this` with its aggregate
  subqueries, so it could not fire under pyshacl at all;
* `DAT-001`'s `xsd:boolean` branch regexed a lexical form rdflib had already
  rewritten, making that branch unreachable (see `checks/literal_typing.py`).

So this module pins the *observed* set. A check that stops firing shows up
as a named id, not as a slightly smaller finding count nobody looks at, and
adding a check without any fixture that trips it is a decision someone has
to write down here rather than an omission.

The sweep runs `--engine sparql`: coverage is about whether a check *can*
fire, every SHACL shape has a portable `.rq` twin (guarded by
`test_check_coverage.py`), and skipping pyshacl takes this module from ~95s
to ~10s.
"""
import pytest
import rdflib

from ontology_suite import config, pipeline
from ontology_suite.checks.registry import Registry

FIXTURES = {
    "domain": ["examples/ontology/domain.ttl"],
    "stress": [
        "examples/checks_stress_test/stress-ontology.ttl",
        "examples/checks_stress_test/stress-data.ttl",
    ],
    # Added for this module: seeds one error per check that neither fixture
    # above trips (LOG-004..007, STR-005, STR-009, STY-004, STY-005). Every
    # seeded error is marked `# SEEDS:` in the file with the id it produces.
    "property_axioms": ["examples/property_axioms/ontology.ttl"],
}

# Observed firing across the fixtures above. Pinned as an exact set: an id
# appearing here that no longer fires is a regression, and an id firing that
# isn't here means a fixture drifted into tripping something new (worth a
# look, then add it).
EXPECTED_FIRING = {
    "DAT-001", "DAT-002", "DAT-003",
    "EFF-001", "EFF-002", "EFF-003",
    "LOG-001", "LOG-002", "LOG-003", "LOG-004", "LOG-005", "LOG-006", "LOG-007",
    "QUA-001", "QUA-002", "QUA-003", "QUA-004", "QUA-007",
    "STR-001", "STR-002", "STR-003", "STR-004", "STR-005",
    "STR-006", "STR-007", "STR-008", "STR-009",
    "STY-001", "STY-002", "STY-003", "STY-004", "STY-005",
}

# Everything else, with why it isn't in the set above. Two different reasons,
# and only the second one is a gap.
NOT_PRODUCED_BY_THE_REGISTRY_SUITE = {
    # dataquality/data_quality.py::conformance_to_rows -- needs a *separate*
    # ontology to compare the data against, which run_registry_suite_on_graph
    # (one merged graph) structurally cannot express. Exercised by
    # tests/test_data_quality.py and tests/test_tarql_output_data_alignment.py.
    "CNF-001", "CNF-002", "CNF-003", "CNF-004", "CNF-005",
    # reasoning/backends/owlrl_backend.py -- rerun against the owlrl closure,
    # not the as-authored graph. Exercised by tests/test_external_reasoner.py.
    "REA-001", "REA-002", "REA-003", "REA-004",
    # reasoning/profile.py -- a syntactic OWL2 profile classification, not a
    # graph pattern. Exercised by the `ontology` stage's profile tests.
    "REA-010", "REA-011", "REA-012",
    # reasoning/backends/external_backend.py -- an external DL reasoner's
    # output. Exercised by tests/test_external_reasoner.py.
    "REA-020", "REA-021", "REA-022",
}

# Registry checks that *are* part of the SPARQL/SHACL suite and that no
# in-repo fixture trips -- the real coverage gap, written down rather than
# left implicit. All three are about the *ontology header*, and they need
# mutually exclusive header states (QUA-005 needs no `owl:Ontology`
# declaration at all; QUA-006 and QUA-008 each need one, malformed in a
# different way), so no single fixture can carry them and they would need
# three more files to close. The sibling fixture repo
# (`consolidated-ontology-suite-python-testing`) covers all three, as
# fixtures 08b, 08c and 08a respectively.
NOT_TRIPPED_BY_ANY_IN_REPO_FIXTURE = {
    "QUA-005",  # no owl:Ontology header
    "QUA-006",  # ontology IRI reused verbatim as the concept namespace
    "QUA-008",  # http:// (not https://) ontology IRI
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


@pytest.fixture(scope="module")
def fired(registry) -> set:
    observed = set()
    for paths in FIXTURES.values():
        graph = rdflib.Graph()
        for path in paths:
            graph.parse(config.REPO_ROOT / path, format="turtle")
        rows = pipeline.run_registry_suite_on_graph(graph, registry, engine="sparql")
        observed |= {r.check_id for r in rows}
    return observed


def test_observed_firing_set_is_unchanged(fired):
    stopped = sorted(EXPECTED_FIRING - fired)
    started = sorted(fired - EXPECTED_FIRING)
    assert stopped == [], (
        f"{stopped} no longer fire against the in-repo fixtures -- either the check "
        "silently stopped matching (the bug class this module exists for) or a fixture changed"
    )
    assert started == [], (
        f"{started} newly fire against the in-repo fixtures -- if that is intended, add them "
        "to EXPECTED_FIRING; if not, a fixture has drifted"
    )


def test_every_registry_id_is_accounted_for(registry, fired):
    """No id may sit outside all three sets: a check is either observed
    firing, explicitly produced by another subsystem, or explicitly recorded
    as untripped. Adding a check to registry.json without deciding which one
    it is fails here."""
    accounted = EXPECTED_FIRING | NOT_PRODUCED_BY_THE_REGISTRY_SUITE | NOT_TRIPPED_BY_ANY_IN_REPO_FIXTURE
    unaccounted = sorted(set(registry.all_ids()) - accounted)
    assert unaccounted == [], (
        f"{unaccounted} are in registry.json but in none of this module's three sets -- "
        "add a fixture that trips them, or record why one doesn't exist"
    )


def test_the_three_sets_do_not_overlap(fired):
    """Guards the bookkeeping itself: an id recorded as untripped that
    actually fires would let a genuine regression hide behind a stale
    comment."""
    assert EXPECTED_FIRING & NOT_PRODUCED_BY_THE_REGISTRY_SUITE == set()
    assert EXPECTED_FIRING & NOT_TRIPPED_BY_ANY_IN_REPO_FIXTURE == set()
    assert NOT_PRODUCED_BY_THE_REGISTRY_SUITE & NOT_TRIPPED_BY_ANY_IN_REPO_FIXTURE == set()
    assert fired & NOT_TRIPPED_BY_ANY_IN_REPO_FIXTURE == set()
