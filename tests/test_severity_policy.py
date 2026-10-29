"""Covers `--severity-policy` / `--stringency` (`checks/severity_policy.py`,
wired into `ontology`/`checks`/`data`/`sketch`/`run`).

The feature exists because `--fail-on` is one global threshold and cannot say
"structural faults block this stage, style does not", so projects were instead
copying the suite's `sparql/` and `shapes/` trees and rewriting
`sh:resultSeverity`/`sh:severity` in place. That approach silently discards the
policy whenever it is applied to only one of a check's two formulations -- the
shape's severity wins and nothing reports the disagreement -- which is the
failure these tests are ultimately against.

The invariant throughout: a policy may change what blocks a build, but it may
never do so invisibly. `declared_severity` keeps what the check said, and the
run prints every re-mapping.
"""
import csv
import json

import pytest

from ontology_suite import cli
from ontology_suite.checks.merge import ResultRow
from ontology_suite.checks.severity_policy import OFF, PolicyError, SeverityPolicy

EX = "https://example.org/mine/"


def _ontology(tmp_path):
    """One class with no label (QUA-001) and one property with neither domain
    nor range (STR-003) -- two checks at two different shipped severities, so
    a policy raising one and lowering the other is observable both ways."""
    path = tmp_path / "mine.ttl"
    path.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f"@prefix ex: <{EX}> .\n"
        f"<{EX}> a owl:Ontology .\n"
        "ex:Widget a owl:Class .\n"
        "ex:looselyRelated a owl:ObjectProperty .\n",
        encoding="utf-8",
    )
    return path


def _policy(tmp_path, body) -> str:
    path = tmp_path / "severities.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def _rows(out_dir):
    with (out_dir / "full_results.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _severity_of(rows, check_id):
    for row in rows:
        if row["check_id"] == check_id:
            return row["severity"], row["declared_severity"]
    return None, None


# ---------------------------------------------------------------------------
# Loading and resolution
# ---------------------------------------------------------------------------
def test_a_flat_object_is_one_unnamed_stage(tmp_path):
    """The simplest useful policy needs no stage vocabulary at all."""
    policy = SeverityPolicy.load(_policy(tmp_path, {"STR-003": "Info"}))
    stage, resolved = policy.resolve(None)
    assert resolved == {"STR-003": "Info"}
    assert len(policy.stage_names()) == 1


def test_a_list_of_single_key_objects_is_accepted(tmp_path):
    """Because it is what people write by hand, and rejecting it would teach
    nothing except that the parser is fussy."""
    policy = SeverityPolicy.load(
        _policy(tmp_path, [{"STR-003": "Info"}, {"QUA-001": "Violation"}])
    )
    _, resolved = policy.resolve(None)
    assert resolved == {"STR-003": "Info", "QUA-001": "Violation"}


def test_stage_names_are_the_files_own(tmp_path):
    """Nothing here knows or prefers any particular set of stage names: the
    suite has no opinion about what a project calls its stringency levels."""
    policy = SeverityPolicy.load(
        _policy(tmp_path, {"stages": {
            "napkin": {"STR-003": "Info"},
            "ready-to-ship": {"STR-003": "Violation"},
            "archival": {"STR-003": "Violation"},
        }})
    )
    assert policy.stage_names() == ["archival", "napkin", "ready-to-ship"]
    assert policy.resolve("napkin")[1] == {"STR-003": "Info"}
    assert policy.resolve("ready-to-ship")[1] == {"STR-003": "Violation"}


def test_extends_inherits_then_overrides(tmp_path):
    """Three stages differing in two checks should not have to restate the
    other forty; the nearest stage wins."""
    policy = SeverityPolicy.load(
        _policy(tmp_path, {"stages": {
            "draft": {"STR-003": "Warning", "QUA-001": "Info"},
            "release": {"extends": "draft", "STR-003": "Violation"},
        }})
    )
    _, resolved = policy.resolve("release")
    assert resolved == {"STR-003": "Violation", "QUA-001": "Info"}


def test_an_extends_cycle_is_refused(tmp_path):
    with pytest.raises(PolicyError, match="cycle"):
        SeverityPolicy.load(
            _policy(tmp_path, {"stages": {
                "a": {"extends": "b"},
                "b": {"extends": "a"},
            }})
        )


def test_several_stages_require_naming_one(tmp_path):
    """Picking a stringency for the caller is exactly the silent failure this
    module exists to prevent, so it refuses instead."""
    policy = SeverityPolicy.load(
        _policy(tmp_path, {"stages": {"a": {"STR-003": "Info"},
                                      "b": {"STR-003": "Violation"}}})
    )
    with pytest.raises(PolicyError, match="--stringency"):
        policy.resolve(None)


def test_an_unknown_stage_lists_the_real_ones(tmp_path):
    policy = SeverityPolicy.load(
        _policy(tmp_path, {"stages": {"draft": {}, "release": {}}})
    )
    with pytest.raises(PolicyError, match="draft, release"):
        policy.resolve("relese")


def test_a_check_listed_twice_in_one_stage_is_refused(tmp_path):
    """Keeping the last would make the file's meaning depend on its order."""
    path = tmp_path / "dup.json"
    path.write_text('[{"STR-003": "Info"}, {"STR-003": "Violation"}]', encoding="utf-8")
    with pytest.raises(PolicyError, match="twice"):
        SeverityPolicy.load(path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_an_unknown_id_is_refused_with_a_near_miss():
    """`STR-0003` for `STR-003` reads as entirely plausible in a diff, and
    matches no finding, so the override would simply never be applied."""
    with pytest.raises(PolicyError, match=r"did you mean STR-003"):
        SeverityPolicy.validate({"STR-0003": "Violation"}, ["STR-003", "QUA-001"])


def test_an_invalid_severity_is_refused_naming_the_valid_ones():
    with pytest.raises(PolicyError, match="not a severity"):
        SeverityPolicy.validate({"STR-003": "Not a worry"}, ["STR-003"])


def test_off_is_valid_and_is_not_a_severity():
    """`off` means "we did not ask", which is deliberately distinct from
    `Info`, meaning "we looked and here it is"."""
    SeverityPolicy.validate({"STR-003": OFF}, ["STR-003"])


# ---------------------------------------------------------------------------
# Application to rows
# ---------------------------------------------------------------------------
def _row(check_id, severity):
    return ResultRow(
        check_id=check_id, category="structural", title="t", severity=severity,
        focus_node=EX + "x", path=None, value=None, message="m", remediation=None,
    )


def test_apply_keeps_what_the_check_declared():
    rows, changes = SeverityPolicy.apply(
        [_row("STR-003", "Warning")], {"STR-003": "Violation"}
    )
    assert rows[0].severity == "Violation"
    assert rows[0].declared_severity == "Warning"
    assert [c.describe() for c in changes] == [
        "STR-003 Warning -> Violation (1 finding(s))"
    ]


def test_apply_leaves_declared_severity_unset_when_nothing_moved():
    """A populated `declared_severity` has to mean "this was re-mapped";
    filling it in unconditionally would make every row look overridden."""
    rows, changes = SeverityPolicy.apply(
        [_row("STR-003", "Warning")], {"STR-003": "Warning"}
    )
    assert rows[0].declared_severity is None
    assert changes == []


def test_off_drops_the_findings():
    rows, changes = SeverityPolicy.apply(
        [_row("STR-003", "Warning"), _row("QUA-001", "Info")], {"STR-003": OFF}
    )
    assert [r.check_id for r in rows] == ["QUA-001"]
    assert changes[0].suppressed


def test_checks_the_policy_does_not_mention_are_untouched():
    rows, _ = SeverityPolicy.apply([_row("QUA-001", "Info")], {"STR-003": OFF})
    assert rows[0].severity == "Info"


# ---------------------------------------------------------------------------
# End to end, through the CLI
# ---------------------------------------------------------------------------
def test_a_stage_changes_what_blocks_the_build(tmp_path):
    """The whole point: one ontology, two stringencies, two exit codes, and
    no edit to any check."""
    ontology = _ontology(tmp_path)
    policy = _policy(tmp_path, {"stages": {
        # STR-003 ships as Warning, so a Violation gate passes it by default.
        "sketch": {"STR-003": "Info"},
        "release": {"STR-003": "Violation"},
    }})

    sketch_out = tmp_path / "out-sketch"
    code = cli.main([
        "checks", "--ontology", str(ontology), "--out-dir", str(sketch_out),
        "--severity-policy", policy, "--stringency", "sketch",
        "--own-namespace", EX, "--fail-on", "Violation",
    ])
    assert code == 0

    release_out = tmp_path / "out-release"
    code = cli.main([
        "checks", "--ontology", str(ontology), "--out-dir", str(release_out),
        "--severity-policy", policy, "--stringency", "release",
        "--own-namespace", EX, "--fail-on", "Violation",
    ])
    assert code == 1

    severity, declared = _severity_of(_rows(release_out), "STR-003")
    assert (severity, declared) == ("Violation", "Warning")


def test_the_csv_records_what_the_check_declared(tmp_path):
    """A reader must be able to tell a stage's reading from the check's own
    without knowing which stage ran."""
    ontology = _ontology(tmp_path)
    out_dir = tmp_path / "out"
    cli.main([
        "checks", "--ontology", str(ontology), "--out-dir", str(out_dir),
        "--severity-policy", _policy(tmp_path, {"STR-003": "Info"}),
        "--own-namespace", EX, "--fail-on", "never",
    ])
    rows = _rows(out_dir)
    assert _severity_of(rows, "STR-003") == ("Info", "Warning")
    # Untouched checks carry an empty column rather than a repeat of their own
    # severity, so "was this re-mapped" stays answerable by looking.
    assert _severity_of(rows, "QUA-001")[1] == ""


def test_a_dual_formulation_is_remapped_once_and_wholly(tmp_path):
    """STR-002 is written as both a shape and a query. Rewriting the check
    files applies to one arm at a time and the shape silently wins; applying
    to finished rows cannot do that, because the join already made them one
    row. `sources` proves both arms were present."""
    path = tmp_path / "undeclared.ttl"
    path.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f"@prefix ex: <{EX}> .\n"
        f"<{EX}> a owl:Ontology .\n"
        "ex:Widget a owl:Class .\n"
        "ex:w1 a ex:Widget ; ex:undeclaredProp \"x\" .\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    cli.main([
        "checks", "--ontology", str(path), "--out-dir", str(out_dir),
        "--severity-policy", _policy(tmp_path, {"STR-002": "Info"}),
        "--own-namespace", EX, "--fail-on", "never", "--engine", "both",
    ])
    rows = _rows(out_dir)
    str002 = [r for r in rows if r["check_id"] == "STR-002"]
    assert str002, "STR-002 did not fire; the fixture no longer exercises this"
    assert all(r["severity"] == "Info" for r in str002)
    assert all(r["declared_severity"] == "Violation" for r in str002)
    assert "shacl" in str002[0]["sources"] and "sparql" in str002[0]["sources"]


def test_it_works_for_a_check_the_suite_never_shipped(tmp_path):
    """A project-local check merged into --registry is an entry like any
    other, so a policy can name it. This is the case that makes the feature
    usable by projects whose house rules are the ones needing a stringency."""
    from ontology_suite import config

    registry = json.loads(
        config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8")
        if hasattr(config.DEFAULT_REGISTRY_PATH, "read_text")
        else open(config.DEFAULT_REGISTRY_PATH, encoding="utf-8").read()
    )
    entries = registry["checks"] if isinstance(registry, dict) else registry
    entries.append({
        "id": "HSE-001",
        "category": "structural",
        "metric": "house",
        "default_severity": "Violation",
        "title": "House rule",
        "description": "A check the suite never shipped.",
        "remediation": "Do the house thing.",
    })
    merged = tmp_path / "registry.json"
    merged.write_text(json.dumps(registry), encoding="utf-8")

    # The policy validates against the registry the run loaded, so a house id
    # resolves exactly as STR-003 does.
    policy = SeverityPolicy.load(_policy(tmp_path, {"HSE-001": "Info"}))
    _, resolved = policy.resolve(None)
    ids = [e["id"] for e in entries]
    SeverityPolicy.validate(resolved, ids)

    rows, changes = SeverityPolicy.apply([_row("HSE-001", "Violation")], resolved)
    assert rows[0].severity == "Info"
    assert rows[0].declared_severity == "Violation"
    assert changes[0].check_id == "HSE-001"


def test_stringency_without_a_policy_is_a_usage_error(tmp_path):
    """There is no default policy to take a stage from, and silently ignoring
    the flag would be the worst of the available answers."""
    ontology = _ontology(tmp_path)
    with pytest.raises(SystemExit):
        cli.main([
            "checks", "--ontology", str(ontology),
            "--out-dir", str(tmp_path / "out"), "--stringency", "release",
        ])


def test_a_bad_policy_exits_2_not_1(tmp_path):
    """Distinguishable in CI from "findings reached --fail-on"."""
    ontology = _ontology(tmp_path)
    code = cli.main([
        "checks", "--ontology", str(ontology), "--out-dir", str(tmp_path / "out"),
        "--severity-policy", _policy(tmp_path, {"STR-0003": "Info"}),
    ])
    assert code == 2
