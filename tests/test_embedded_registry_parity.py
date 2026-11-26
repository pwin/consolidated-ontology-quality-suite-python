"""The check registry has a third copy now, inside the engines.

`resources/registry.json` here, `resources/checks-registry/registry.json` in
the VS Code extension, and one embedded in SemOps System's `semops-checks`
crate, reachable from Python as `semops_system.list_checks()`. The first two
are held character-for-character identical by
`tests/test_shared_registry_parity.py`. Nothing compared either against the
third, so it could drift without a test noticing -- which is exactly how the
first two came to differ in five fields and three whole entries, found by
diffing rather than by anything failing.

This closes that. It is a weaker comparison than the file one on purpose: the
embedded copy is built from a Rust crate and reaches Python through a
serialisation boundary, so "the same bytes" is not a property it can have.
What it can have, and what a reader of either copy depends on, is the same set
of ids carrying the same severity and the same prose. That is what is compared.

Only the fields both copies claim to hold are compared. The embedded copy is
not obliged to carry this repo's `sources` or its file paths -- it has its own
view of which engine runs what -- and a test that demanded them would fail on
a difference that is not drift. If it starts carrying them, extend the list.

Skipped only if `semops_system` has no `list_checks`, which would mean the
engines predate the embedded registry.
"""
import json

import pytest

from ontology_suite import config

semops_system = pytest.importorskip("semops_system")

pytestmark = pytest.mark.skipif(
    not hasattr(semops_system, "list_checks"),
    reason="this semops-system does not embed a check registry",
)

#: The fields both copies hold and a reader relies on. `default_severity`
#: drives report badges and cucumber tags here, so a disagreement about it is
#: a disagreement about what a build is told.
SHARED_FIELDS = (
    "category",
    "metric",
    "default_severity",
    "title",
    "description",
    "remediation",
)


def _ours():
    data = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
    checks = data["checks"] if isinstance(data, dict) and "checks" in data else data
    return {c["id"]: c for c in checks}


def _embedded():
    return {c["id"]: c for c in semops_system.list_checks()}


def test_the_same_checks_exist_in_both():
    ours, theirs = _ours(), _embedded()
    missing_there = sorted(set(ours) - set(theirs))
    missing_here = sorted(set(theirs) - set(ours))
    assert not missing_there, (
        f"this repo declares checks the engines' embedded registry does not: "
        f"{missing_there}. Either the engines are behind, or a check was added "
        f"here without being added there."
    )
    assert not missing_here, (
        f"the engines' embedded registry declares checks this repo does not: "
        f"{missing_here}. A check added in SemOps System has to arrive here too, "
        f"or `--engine` will run something this repo cannot name."
    )


@pytest.mark.parametrize("field", SHARED_FIELDS)
def test_every_shared_field_agrees(field):
    ours, theirs = _ours(), _embedded()
    differences = [
        (cid, ours[cid].get(field), theirs[cid].get(field))
        for cid in sorted(set(ours) & set(theirs))
        if ours[cid].get(field) != theirs[cid].get(field)
    ]
    assert not differences, (
        f"{len(differences)} check(s) disagree on {field!r} between this repo and "
        f"the engines' embedded registry. First: {differences[0]}. Whichever copy "
        f"is right, both must carry it -- the registry is what a reader of a "
        f"report consults."
    )


def test_the_count_is_the_one_the_rest_of_the_suite_pins():
    """61 is asserted by `test_check_coverage.py` and quoted in the docs, so
    pin it here too: a count that moves in the engines and not here is the
    drift this module exists to catch."""
    assert len(_embedded()) == len(_ours()) == 61
