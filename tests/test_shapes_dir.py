"""`--shapes` replaces the suite's shapes, and now says so.

This was the last of the three override flags to drop checks silently.
`--sparql` got a warning in 0.18.0, after a real CI gate was found running 8
of 61 checks and reporting clean. `--registry` got its own measured note after
an unmerged fragment turned 60 findings into 58. `--shapes` had neither, and
it is the one flag that *cannot* be fixed by passing it twice: the engine
loads every `*.ttl` in one directory into a single shapes graph, so there is
no composing form to recommend.

Measured on `examples/riverside_library` with `--engine shacl` before the
warning existed: 37 findings with the built-in shapes, 1 with a house
directory holding a single shape, and nothing said either way.
"""
from pathlib import Path

import pytest

from ontology_suite import cli, config
from ontology_suite.checks.shacl_runner import declared_check_ids

HOUSE_SHAPE = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix oq:   <https://semantechs.co.uk/ontology-quality/> .
@prefix lib:  <https://example.org/riverside/> .

oq:LIB-001
  a sh:NodeShape ;
  rdfs:label "LIB-001: book missing an ISBN" ;
  sh:targetClass lib:Book ;
  sh:severity sh:Violation ;
  sh:sparql [
    a sh:SPARQLConstraint ;
    sh:message "Book {$this} has no lib:isbn." ;
    sh:select "PREFIX lib: <https://example.org/riverside/> SELECT $this WHERE { FILTER NOT EXISTS { $this lib:isbn ?i } }" ;
  ] .
"""


@pytest.fixture
def house_only(tmp_path):
    """A project's own shapes directory, with nothing of the suite's in it."""
    d = tmp_path / "house-only"
    d.mkdir()
    (d / "house.ttl").write_text(HOUSE_SHAPE, encoding="utf-8")
    return d


@pytest.fixture
def merged(tmp_path):
    """The arrangement the warning recommends: the suite's shapes copied in
    alongside the project's, since there is no second flag to pass them in."""
    d = tmp_path / "merged"
    d.mkdir()
    for ttl in config.DEFAULT_SHAPES_DIR.glob("*.ttl"):
        (d / ttl.name).write_text(ttl.read_text(encoding="utf-8"), encoding="utf-8")
    (d / "house.ttl").write_text(HOUSE_SHAPE, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# counting the checks a shapes directory declares
# ---------------------------------------------------------------------------
def test_split_shapes_count_once():
    """`STY-002-data` and `STY-002-obj` are two shapes for one check, tied
    together by `oq:checkId`. Counting subjects would say 23 where a run
    reports 21, and a warning quoting 23 would be worse than no warning."""
    ids = declared_check_ids(config.DEFAULT_SHAPES_DIR)
    assert len(ids) == 21
    assert {i for i in ids if i.startswith("STY-002")} == {"STY-002"}


def test_a_project_shape_is_counted_even_though_the_registry_has_never_heard_of_it(house_only):
    """The count must not be filtered through the shipped registry: a house
    check's id is not in it, and it is exactly what the reader is adding."""
    assert declared_check_ids(house_only) == {"LIB-001"}


# ---------------------------------------------------------------------------
# when the warning fires
# ---------------------------------------------------------------------------
def test_replacing_the_suite_s_shapes_warns_and_counts_both_sides(house_only):
    warnings = []
    cli._resolve_shapes_dir(str(house_only), warnings)
    assert len(warnings) == 1
    assert "1 project shape-backed check(s) will run" in warnings[0]
    assert "21 built-in one(s) will not" in warnings[0]


def test_the_warning_names_the_copy_and_not_a_second_flag(house_only):
    """`--sparql`'s warning says "pass it again". This one cannot: there is no
    composing form. It has to name the merged directory instead, and the path
    of the installed shapes, since a reader cannot guess where a wheel put
    them."""
    warnings = []
    cli._resolve_shapes_dir(str(house_only), warnings)
    assert "not repeatable" in warnings[0]
    assert "one directory" in warnings[0]
    assert str(config.DEFAULT_SHAPES_DIR) in warnings[0]


# ---------------------------------------------------------------------------
# when it stays quiet
# ---------------------------------------------------------------------------
def test_the_default_does_not_warn():
    warnings = []
    assert cli._resolve_shapes_dir(None, warnings) == str(config.DEFAULT_SHAPES_DIR)
    assert warnings == []


def test_builtin_names_the_installed_shapes_without_a_path():
    """Accepted for symmetry with `--sparql`, so a committed config can name
    the installed tree rather than a path that is wrong on another machine."""
    warnings = []
    assert cli._resolve_shapes_dir(cli.BUILTIN_QUERIES, warnings) == str(config.DEFAULT_SHAPES_DIR)
    assert warnings == []


def test_the_suite_s_own_directory_spelled_awkwardly_does_not_warn():
    """A gate config writes an absolute path, a shell a relative one, and
    Windows either with backslashes. Comparing strings would warn on a run
    that did include the built-in shapes."""
    warnings = []
    awkward = str(config.DEFAULT_SHAPES_DIR / "." / ".." / config.DEFAULT_SHAPES_DIR.name)
    cli._resolve_shapes_dir(awkward, warnings)
    assert warnings == [], f"the same directory spelled differently warned: {warnings}"


def test_a_merged_directory_does_not_warn(merged):
    """The whole point of the warning is to get the reader here, so arriving
    must be silent."""
    warnings = []
    cli._resolve_shapes_dir(str(merged), warnings)
    assert warnings == []
    assert declared_check_ids(merged) == declared_check_ids(config.DEFAULT_SHAPES_DIR) | {"LIB-001"}


# ---------------------------------------------------------------------------
# the warning must never be the thing that breaks a run
# ---------------------------------------------------------------------------
def test_an_unreadable_shapes_directory_does_not_raise_from_the_warning(tmp_path):
    """A malformed `.ttl` should fail where the shapes are actually loaded,
    with that error -- not here, inside a best-effort count, with a parse
    error that mentions neither the flag nor the file the user cares about."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "broken.ttl").write_text("this is not turtle {{{", encoding="utf-8")
    warnings = []
    assert cli._resolve_shapes_dir(str(bad), warnings) == str(bad)
    assert warnings == []


def test_a_missing_shapes_directory_is_named_as_the_fault_it_is(tmp_path):
    """Not silence, and not the generic replacement message either. A path
    that is not there loads no shapes, and telling the reader to copy the
    built-ins into a directory that does not exist would send them the wrong
    way. Say which thing is actually wrong."""
    missing = tmp_path / "nope"
    warnings = []
    assert cli._resolve_shapes_dir(str(missing), warnings) == str(missing)
    assert len(warnings) == 1
    assert "is not a directory" in warnings[0]
    assert "built-in" not in warnings[0], "the generic message would misdirect here"
