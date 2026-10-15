"""Six defects where the suite said too little, too much, or the wrong thing.

Reported from use, not caught here, and they share a shape worth naming: each
is a case where the output *looked* fine. A caveat filtered away, a typo that
compared nothing and exited 0, one contradiction counted twice, a hint pointing
at the wrong fix. None of them raised, and three of them made a run look
cleaner or more complete than it was.

Two were defects in earlier work in this same file's neighbourhood, which is
the useful part: the `--sparql` warning told the reader to pass an absolute
path in the same release that added `@builtin` precisely because a path is
wrong on every other machine, and the blank-node guard in the namespace
warning was pinned by a test feeding it a string shape the pipeline never
produces.
"""
import json

import pytest
from rdflib import BNode

from ontology_suite import cli
from ontology_suite.checks.merge import (
    RUN_LEVEL_FOCUS,
    RUN_LEVEL_FOCUS_IRI,
    ResultRow,
    is_run_level,
    merge_across_stages,
)
from ontology_suite.remote.manifest import VALID_GRAPH_ROLES, GraphManifest


def row(focus="https://acme.example.org/ns/Robot", **kw):
    base = dict(
        check_id="STR-001", category="structural", title="t", severity="Warning",
        focus_node=focus, path=None, value=None, message="m", remediation=None,
        sources=["sparql"],
    )
    base.update(kw)
    return ResultRow(**base)


# ---------------------------------------------------------------------------
# a scoped run kept dropping the "I could not check everything" finding
# ---------------------------------------------------------------------------
def test_a_run_level_finding_survives_a_scoped_run():
    """REA-022 says the external DL reasoner could not run, so what follows is
    sound but not complete. Its focus node is the sentinel `oq:Graph`, which
    starts with nobody's namespace, so every `--own-namespace` run dropped it.

    That is the worst case for this particular finding: the output looked
    complete *because* the caveat had been filtered out of it.
    """
    warnings = []
    kept = cli._filter_own_namespace(
        [row(), row(focus=RUN_LEVEL_FOCUS, check_id="REA-022", severity="Info")],
        "https://acme.example.org/ns/", warnings,
    )
    assert {r.check_id for r in kept} == {"STR-001", "REA-022"}
    assert warnings == []


def test_a_scoped_run_that_keeps_only_the_caveat_still_warns():
    """Keeping the caveat must not make an empty run look like a match. If the
    only survivor is the run-level finding, nothing in the project's namespace
    matched and the near-miss warning is still the useful thing to say."""
    warnings = []
    kept = cli._filter_own_namespace(
        [row(), row(focus=RUN_LEVEL_FOCUS, check_id="REA-022", severity="Info")],
        "http://wrong.example/ns#", warnings,
    )
    assert [r.check_id for r in kept] == ["REA-022"]
    assert len(warnings) == 1 and "matched none of the" in warnings[0]


def test_every_graph_wide_check_survives_a_scoped_run():
    """The first fix for this exempted one constant, which kept REA-022 and
    went on dropping the other three.

    `oq:Graph` is a suite-wide convention, not a reasoning detail: EFF-002
    (blank-node ratio), QUA-005 and STY-005 (separator style) anchor on it too,
    because a graph-wide statistic has no natural focus node. And the two arms
    spell it differently -- the queries and shapes write `oq:Graph` against a
    declared prefix, so rdflib hands back the expanded IRI, while the Python
    backends write the prefixed name unexpanded. Comparing against the Python
    spelling alone therefore fixed exactly the one finding that had been
    reported and left three silently filtered.
    """
    rows = [
        row(),
        row(focus=RUN_LEVEL_FOCUS, check_id="REA-022", severity="Info"),
        row(focus=RUN_LEVEL_FOCUS_IRI, check_id="EFF-002"),
        row(focus=RUN_LEVEL_FOCUS_IRI, check_id="QUA-005"),
        row(focus=RUN_LEVEL_FOCUS_IRI, check_id="STY-005"),
    ]
    kept = cli._filter_own_namespace(rows, "https://acme.example.org/ns/", [])
    assert {r.check_id for r in kept} == {
        "STR-001", "REA-022", "EFF-002", "QUA-005", "STY-005"
    }


def test_both_spellings_of_the_anchor_are_recognised():
    """Stated as the property, so a later tidy-up that collapses the two
    spellings onto one cannot quietly re-break the other."""
    for spelling in (RUN_LEVEL_FOCUS, RUN_LEVEL_FOCUS_IRI):
        assert is_run_level(spelling), spelling
    assert not is_run_level("https://acme.example.org/ns/Robot")
    assert not is_run_level("oq:Graphs"), "a prefix match would be too loose"


def test_the_two_rows_the_backend_emits_use_the_shared_constant():
    """REA-020 took the constant and REA-022 kept the literal, so the two rows
    agreed only by coincidence. Read off the rows rather than the source."""
    from ontology_suite.reasoning.backends import external_backend

    assert external_backend.unavailable_row("hermit", "x").focus_node == RUN_LEVEL_FOCUS
    assert external_backend._inconsistent_ontology_row("hermit").focus_node == RUN_LEVEL_FOCUS


# ---------------------------------------------------------------------------
# the namespace hint offered a blank node as a namespace
# ---------------------------------------------------------------------------
def test_a_real_blank_node_is_not_offered_as_a_namespace():
    """The guard tested `focus_node.startswith("_:")`, and `str(BNode())` is a
    bare label -- `N094f676089dd497bbcc34c900805dc6a`, no prefix. So the guard
    never fired on real data, and the derivation turned the label into
    `N094f.../` and listed it as a namespace someone might have meant.

    It was pinned by a test feeding it `_:0_b56`, which the pipeline does not
    produce. The test passed while the code did nothing.
    """
    warnings = []
    cli._filter_own_namespace(
        [row(focus=str(BNode())), row()], "http://wrong.example/ns#", warnings,
    )
    offered = warnings[0].split("seen: ", 1)[1]
    assert "https://acme.example.org/ns/" in offered
    assert "N" not in offered.split(",")[0] or "://" in offered.split(",")[0], (
        f"a blank node label was offered as a namespace: {offered}"
    )


def test_every_namespace_offered_has_a_scheme():
    """Stated as the property rather than as "not a blank node": a namespace
    has a scheme, and anything without one cannot be what the caller meant."""
    warnings = []
    cli._filter_own_namespace(
        [row(focus=str(BNode())), row(focus=str(BNode())), row()],
        "http://wrong.example/ns#", warnings,
    )
    offered = [s.strip() for s in warnings[0].split("seen: ", 1)[1].split(",")]
    assert all("://" in s for s in offered if not s.startswith("(+")), offered


# ---------------------------------------------------------------------------
# the hint named a path in the release that added @builtin to avoid paths
# ---------------------------------------------------------------------------
def test_the_sparql_hint_names_the_portable_answer(tmp_path):
    """`--sparql mine` warns that the built-in tree was replaced. It told the
    reader to pass the absolute path of the packaged queries -- in the same
    release that added `@builtin` because a committed absolute path is wrong on
    every other machine and wrong again after an upgrade."""
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "CMP-001.rq").write_text("CONSTRUCT {} WHERE {}", encoding="utf-8")

    warnings = []
    cli._resolve_sparql_dirs([str(mine)], warnings)
    assert len(warnings) == 1
    assert cli.BUILTIN_QUERIES in warnings[0]
    assert "resources" not in warnings[0], (
        f"the hint still names a filesystem path: {warnings[0]}"
    )


# ---------------------------------------------------------------------------
# one contradiction, reported twice
# ---------------------------------------------------------------------------
def test_one_finding_reached_by_two_routes_is_one_row():
    """`build_unified_results` deduplicates within a call. `data` and `run`
    join two of them -- the registry suite over the asserted graph, the
    reasoning stage over the owlrl closure -- and nothing deduplicated across
    the join, so one contradiction came back as two LOG-001 rows differing
    only in `sources`."""
    merged = merge_across_stages([
        row(check_id="LOG-001", severity="Violation", path="p", value="v", sources=["sparql"]),
        row(check_id="LOG-001", severity="Violation", path="p", value="v",
            sources=["owlrl-closure", "sparql"]),
    ])
    assert len(merged) == 1
    assert merged[0].sources == ["owlrl-closure", "sparql"], (
        "both routes must be recorded, which is what the dedup key means elsewhere"
    )


def test_genuinely_different_findings_are_not_merged():
    merged = merge_across_stages([
        row(check_id="LOG-001", focus_node="https://x/A"),
        row(check_id="LOG-001", focus_node="https://x/B"),
        row(check_id="STR-001", focus_node="https://x/A"),
    ])
    assert len(merged) == 3


def test_blank_focus_rows_pass_through_untouched():
    """Their keys are assigned by ordinal within a call, so re-keying them here
    would pair up rows the within-call logic deliberately kept apart."""
    rows = [
        row(check_id="LOG-001", focus_node=str(BNode()), focus_is_blank=True),
        row(check_id="LOG-001", focus_node=str(BNode()), focus_is_blank=True),
    ]
    assert len(merge_across_stages(rows)) == 2


def test_merging_preserves_order():
    """The stage sorts afterwards, but a merge that shuffled rows would make
    every report diff against itself."""
    rows = [row(check_id=c, focus_node=f"https://x/{c}") for c in ("C", "A", "B")]
    assert [r.check_id for r in merge_across_stages(rows)] == ["C", "A", "B"]


# ---------------------------------------------------------------------------
# a mistyped manifest role compared nothing and exited 0
# ---------------------------------------------------------------------------
def _manifest(tmp_path, *roles):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "graphs": [{"graph_uri": f"urn:g{i}", "role": r} for i, r in enumerate(roles)]
    }), encoding="utf-8")
    return path


def test_a_mistyped_role_is_refused_at_load(tmp_path):
    """`GraphRole` is a typing Literal, which nothing enforces at runtime, so
    `triplifed_data` loaded happily and then matched neither binding list. The
    run compared nothing and exited 0 -- a config typo reading as a clean bill
    of health."""
    with pytest.raises(ValueError, match="unknown graph role"):
        GraphManifest.load(_manifest(tmp_path, "ontology", "triplifed_data"))


def test_the_message_names_every_bad_role_and_the_valid_set(tmp_path):
    """Fixing one typo only to be told about the next is its own small
    cruelty, and the valid set is the thing the reader needs."""
    with pytest.raises(ValueError) as excinfo:
        GraphManifest.load(_manifest(tmp_path, "Ontology", "triplifed_data"))
    message = str(excinfo.value)
    assert "Ontology" in message and "triplifed_data" in message
    for role in VALID_GRAPH_ROLES:
        assert role in message


def test_role_matching_is_case_sensitive_and_says_so(tmp_path):
    """`Ontology` is not `ontology`. Silently accepting it would be a different
    kind of wrong, so it is refused by name."""
    with pytest.raises(ValueError, match="Ontology"):
        GraphManifest.load(_manifest(tmp_path, "Ontology"))


def test_a_valid_manifest_still_loads(tmp_path):
    manifest = GraphManifest.load(_manifest(tmp_path, "ontology", "triplified_data"))
    assert len(manifest.bindings) == 2
    assert len(manifest.ontology_bindings()) == 1
    assert len(manifest.data_bindings()) == 1


# ---------------------------------------------------------------------------
# three names for "make this fail the build"
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command,required,dest,old_spelling",
    [
        ("consistency", ["--new", "x.ttl"], "fail_on_misalignment", "--fail-on-misalignment"),
        ("consistency-remote", ["--manifest", "m.json", "--query-endpoint", "http://e"],
         "fail_on_misalignment", "--fail-on-misalignment"),
        ("pattern-consistency", ["--queries", "q", "--ontology", "o.ttl", "--taxonomy", "t.ttl"],
         "fail_on_mismatch", "--fail-on-mismatch"),
    ],
)
def test_one_spelling_works_everywhere_and_the_old_one_still_does(
    command, required, dest, old_spelling
):
    """Every other subcommand takes `--fail-on <severity>`; these three took
    `--fail-on-misalignment` or `--fail-on-mismatch`. Three names for the first
    flag anyone reaches for in CI.

    The old names are aliases rather than renames, because a committed CI
    config or gate.yml using one has to keep working.
    """
    parser = cli.build_arg_parser()
    assert getattr(parser.parse_args([command, *required, "--fail-on-anything"]), dest) is True
    assert getattr(parser.parse_args([command, *required, old_spelling]), dest) is True
    assert getattr(parser.parse_args([command, *required]), dest) is False
