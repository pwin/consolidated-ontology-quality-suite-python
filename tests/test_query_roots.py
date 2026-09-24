"""`--sparql` composes query trees instead of silently replacing one.

The flag took a single directory, so `--sparql my-checks` ran the project's
queries *instead of* the suite's 42 and said nothing about it. Found in that
state: a CI gate whose config pointed at eight project checks, whose merged
registry declared 61, and which reported a clean run having skipped almost
everything it was built to catch. The registry is a declaration of what each
check means; the directories decide what runs; nothing connected the two.

Two halves to the fix, and the second matters more than the first. The flag is
repeatable, so trees compose. And a run that does not include the suite's own
tree says so, because "only your checks ran" is sometimes exactly right -- the
competency harness runs one tree at a time deliberately -- and the failure was
never the behaviour, it was the silence.
"""
import json

import pytest

from ontology_suite import cli, config
from ontology_suite.checks.sparql_runner import SUBJECT_SPECIFIC_DIRS, as_dirs, discover_queries

QUERY = (
    "PREFIX sh: <http://www.w3.org/ns/shacl#>\n"
    "PREFIX oq: <https://semantechs.co.uk/ontology-quality/>\n"
    "CONSTRUCT { _:r a sh:ValidationResult } WHERE { FILTER(false) }\n"
)


def _tree(root, *names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(QUERY, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# as_dirs
# ---------------------------------------------------------------------------
def test_a_string_is_one_path_not_a_sequence_of_characters():
    """The bug this helper exists to make impossible."""
    assert len(as_dirs("some/dir")) == 1
    assert len(as_dirs(["a", "b"])) == 2


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def test_one_root_behaves_exactly_as_before(tmp_path):
    root = _tree(tmp_path / "a", "X-001.rq", "sub/X-002.rq")
    assert len(discover_queries(root)) == 2


def test_two_roots_run_both(tmp_path):
    mine = _tree(tmp_path / "mine", "CMP-001.rq")
    theirs = _tree(tmp_path / "theirs", "STR-001.rq", "STR-002.rq")
    assert len(discover_queries([mine, theirs])) == 3


def test_overlapping_roots_run_each_query_once(tmp_path):
    """A project tree that copies part of the suite's, or the same tree given
    twice, must not report every finding twice."""
    root = _tree(tmp_path / "a", "X-001.rq")
    assert len(discover_queries([root, root])) == 1


def test_subject_specific_dirs_are_filtered_per_root(tmp_path):
    """`tarql/` holds queries over a graph nobody else builds. Filtering has to
    be relative to each root, or adding a second root changes whether the
    first one's tarql queries are excluded."""
    assert SUBJECT_SPECIFIC_DIRS == ("tarql",)
    a = _tree(tmp_path / "a", "X-001.rq", "tarql/TQL-001.rq")
    b = _tree(tmp_path / "b", "Y-001.rq")
    found = {p.name for p in discover_queries([a, b])}
    assert found == {"X-001.rq", "Y-001.rq"}


def test_pointing_straight_at_a_tarql_dir_still_runs_it(tmp_path):
    """What the sketch stage does once it has built the BIND facts graph."""
    a = _tree(tmp_path / "a", "tarql/TQL-001.rq")
    assert len(discover_queries(a / "tarql")) == 1


def test_a_missing_root_is_not_an_error(tmp_path):
    """Composition means naming trees that may not exist in every checkout."""
    real = _tree(tmp_path / "real", "X-001.rq")
    assert len(discover_queries([real, tmp_path / "nope"])) == 1


# ---------------------------------------------------------------------------
# the resolver, and its warning
# ---------------------------------------------------------------------------
def test_no_flag_means_the_suite_s_own_tree(tmp_path):
    warnings = []
    assert cli._resolve_sparql_dirs(None, warnings) == [str(config.DEFAULT_SPARQL_DIR)]
    assert warnings == []


def test_the_default_is_not_appended_to_a_given_value(tmp_path):
    """argparse's `action="append"` appends to a non-None default, which would
    have quietly re-added the built-in tree to every explicit `--sparql` and
    made the flag impossible to use narrowly. The default is resolved here
    instead, and this pins that."""
    mine = _tree(tmp_path / "mine", "CMP-001.rq")
    warnings = []
    assert cli._resolve_sparql_dirs([str(mine)], warnings) == [str(mine)]


def test_replacing_the_suite_s_tree_warns_and_counts_both_sides(tmp_path):
    mine = _tree(tmp_path / "mine", "CMP-001.rq")
    warnings = []
    cli._resolve_sparql_dirs([str(mine)], warnings)
    assert len(warnings) == 1
    assert "1 project check(s) ran" in warnings[0]
    # The number that was actually lost, not a vague "some".
    built_in = len(discover_queries(config.DEFAULT_SPARQL_DIR))
    assert f"{built_in} built-in check(s) did not" in warnings[0]
    assert str(config.DEFAULT_SPARQL_DIR) in warnings[0], "the hint must name the path to add"


def test_composing_with_the_suite_s_tree_does_not_warn(tmp_path):
    mine = _tree(tmp_path / "mine", "CMP-001.rq")
    warnings = []
    resolved = cli._resolve_sparql_dirs([str(mine), str(config.DEFAULT_SPARQL_DIR)], warnings)
    assert len(resolved) == 2
    assert warnings == []


def test_the_suite_s_tree_is_recognised_however_it_is_spelled(tmp_path):
    """A gate config writes an absolute path, a shell writes a relative one,
    and Windows writes either with backslashes. Comparing the strings would
    warn on a run that did include the built-ins."""
    warnings = []
    awkward = str(config.DEFAULT_SPARQL_DIR / "." / ".." / config.DEFAULT_SPARQL_DIR.name)
    cli._resolve_sparql_dirs([awkward], warnings)
    assert warnings == [], f"the same directory spelled differently warned: {warnings}"


# ---------------------------------------------------------------------------
# the same trap, one tier over
# ---------------------------------------------------------------------------
def test_the_data_stage_takes_a_shapes_dir(tmp_path):
    """`data` ran the packaged SHACL shapes whatever was asked for: a project
    could pass its own to `checks` and not to `data`, which is the tier that
    looks at its output."""
    import inspect

    from ontology_suite import pipeline

    signature = inspect.signature(pipeline.run_data_stage)
    assert "shapes_dir" in signature.parameters
    assert signature.parameters["shapes_dir"].default == config.DEFAULT_SHAPES_DIR


@pytest.mark.parametrize("command", ["ontology", "checks", "sketch", "data", "run"])
def test_every_report_writing_command_takes_the_flag_more_than_once(command):
    parser = cli.build_arg_parser()
    args = parser.parse_args([command, *_minimal_args(command), "--sparql", "a", "--sparql", "b"])
    assert args.sparql == ["a", "b"], f"{command} does not accept a repeated --sparql"


def _minimal_args(command):
    if command in ("ontology", "docgen"):
        return ["--ontology", "x.ttl"]
    if command == "sketch":
        return ["--queries", "q"]
    if command == "data":
        return ["x.ttl"]
    return []


def test_builtin_is_a_symbol_for_the_suite_s_own_tree():
    """A committed config cannot write the absolute path: an editable install
    resolves it to the source checkout and a wheel to site-packages, and the
    config cannot know which it is looking at. Found when a gate config
    hardcoded a site-packages path that did not exist in an editable
    checkout, so the gate reported skipping every built-in check."""
    warnings = []
    assert cli._resolve_sparql_dirs([cli.BUILTIN_QUERIES], warnings) == [
        str(config.DEFAULT_SPARQL_DIR)
    ]
    assert warnings == []


def test_builtin_composes_with_a_project_tree(tmp_path):
    mine = _tree(tmp_path / "mine", "CMP-001.rq")
    warnings = []
    resolved = cli._resolve_sparql_dirs([str(mine), cli.BUILTIN_QUERIES], warnings)
    assert resolved == [str(mine), str(config.DEFAULT_SPARQL_DIR)]
    assert warnings == [], "the built-in tree is present, so there is nothing to warn about"
