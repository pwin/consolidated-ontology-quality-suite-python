"""Tests for the TARQL BIND review (`sketch/bind_analysis.py`).

Two things here are load-bearing and everything else is detail.

The first is **skeleton comparison**. Comparing BIND expressions as raw text
reports every difference, including the ordinary one where two files feed the
same template from a differently-named CSV column -- which is most of them,
and which makes the check noise. Measured on a real ten-query folder: 37 target
variables are bound in more than one file, 8 of those with differing
expression text, and skeleton comparison reports 7 -- dropping the one case
where two files feed an identical template from differently-named columns.
The `?surface_IRI` case in the fixture pins that behaviour from the other
side: same template, different source variable, no finding.

The second is **comment and literal handling**. These queries build IRIs out
of string literals containing `#`, `{`, quotes and backslashes, and they
reference namespaces whose IRIs contain `#`. A naive comment strip or a
regex-to-the-first-paren silently mangles exactly the expressions this module
exists to compare, and the damage looks like a clean result rather than an
error.
"""
import glob

import pytest

from ontology_suite import config, pipeline
from ontology_suite.sketch import bind_analysis as ba

FIXTURE = sorted(glob.glob(str(config.REPO_ROOT / "examples/tarql_drift/*.rq")))


@pytest.fixture(scope="module")
def report():
    return ba.analyse(FIXTURE)


# --------------------------------------------------------------------------
# The three checks
# --------------------------------------------------------------------------
def test_each_check_fires_exactly_once_on_the_fixture(report):
    from collections import Counter

    counts = Counter(row.check_id for row in ba.bind_report_to_rows(report))
    assert counts == {"TQL-001": 1, "TQL-002": 1, "TQL-003": 1}


def test_drift_names_both_competing_expressions(report):
    assert len(report.drift) == 1
    group = report.drift[0]
    assert group.target == "road_IRI"
    skeletons = [skel for skel, _ in group.variants]
    assert len(skeletons) == 2
    # The reviewer has to be able to see *what* differs without opening the
    # files, so both templates appear in the message.
    message = ba.bind_report_to_rows(report)[0].message
    assert "REPLACE" in message
    assert "roads_to_rdf.rq" in ba.format_bind_report(report)


def test_unbound_constructed_iri_is_a_violation_not_an_info(report):
    rows = {row.check_id: row for row in ba.bind_report_to_rows(report)}
    assert rows["TQL-002"].focus_node == "?direction_IRI"
    assert rows["TQL-002"].severity == "Violation"
    # A plain column name is the same structural situation and must not be
    # reported at the same severity, or the finding that matters is buried.
    assert rows["TQL-003"].focus_node == "?roadname"
    assert rows["TQL-003"].severity == "Info"


# --------------------------------------------------------------------------
# Skeleton comparison -- the design decision the check rests on
# --------------------------------------------------------------------------
def test_same_template_different_source_variable_is_not_drift(report):
    """?surface_IRI is bound in both fixture files from differently-named
    columns through an identical template. Reporting it would be wrong."""
    assert "surface_IRI" not in {group.target for group in report.drift}
    assert ("surface_IRI", 2) in report.shared_and_consistent


def test_skeleton_keeps_literals_and_structure():
    same = ba.skeleton('CONCAT("exd:_Road_", ?a)')
    renamed = ba.skeleton('CONCAT("exd:_Road_",   ?b)')
    assert same == renamed, "variable names and whitespace must not distinguish"

    typo = ba.skeleton('CONCAT("exd:_Rodd_", ?a)')
    wrapped = ba.skeleton('CONCAT("exd:_Road_", REPLACE(?a, " ", "_"))')
    assert typo != same, "a literal typo changes the minted IRI and must show"
    assert wrapped != same, "an extra call changes the minted IRI and must show"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def test_hash_inside_an_iri_or_literal_is_not_a_comment():
    text = (
        'prefix geo: <http://www.opengis.net/ont/geosparql#>\n'
        'WHERE { BIND(CONCAT("a#b", ?x) AS ?y) }\n'
    )
    stripped = ba.strip_comments(text)
    assert "geosparql#" in stripped, "a # inside an IRI must survive"
    assert 'a#b' in stripped, "a # inside a string literal must survive"
    binds = ba.extract_binds(stripped, "t.rq")
    assert [b.target for b in binds] == ["y"]


def test_a_real_comment_is_removed_without_shifting_line_numbers():
    text = "line one\n# BIND(1 AS ?ghost)\nWHERE { BIND(2 AS ?real) }\n"
    stripped = ba.strip_comments(text)
    binds = ba.extract_binds(stripped, "t.rq")
    assert [b.target for b in binds] == ["real"], "a commented-out BIND must not count"
    assert binds[0].line == 3, "offsets must be preserved so line numbers stay true"


def test_nested_calls_parse_to_the_outer_paren():
    """A non-greedy regex stops at the first `)`, which truncates every
    expression of the shape this module is built for."""
    text = 'BIND(tarql:expandPrefixedName(CONCAT("x:_A_", REPLACE(?a, ?b, "_"))) as ?out)'
    binds = ba.extract_binds(text, "t.rq")
    assert len(binds) == 1
    assert binds[0].target == "out"
    assert binds[0].expression.endswith('"_")))')
    assert binds[0].expression.startswith("tarql:expandPrefixedName")


def test_braces_inside_string_literals_do_not_break_block_matching():
    text = 'CONSTRUCT { ?s ?p "a { brace" } WHERE { BIND(1 AS ?s) }'
    facts_binds = ba.extract_binds(text, "t.rq")
    assert [b.target for b in facts_binds] == ["s"]


# --------------------------------------------------------------------------
# Registry and pipeline wiring
# --------------------------------------------------------------------------
def test_rows_match_their_registry_entries(report):
    import json

    registry = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
    declared = {c["id"]: c for c in registry["checks"]}
    for row in ba.bind_report_to_rows(report):
        assert row.check_id in declared, f"{row.check_id} is not in registry.json"
        entry = declared[row.check_id]
        assert row.severity == entry["default_severity"]
        assert row.category == entry["category"] == "tarql"


def test_sketch_stage_reports_the_findings_and_writes_the_review(tmp_path):
    """The review runs on the queries alone -- no --ontology needed, because
    it reads query source rather than the sketch graph."""
    stage = pipeline.run_sketch_stage(
        str(config.REPO_ROOT / "examples/tarql_drift"), tmp_path, query_pattern="*.rq",
    )
    ids = {row.check_id for row in stage.rows}
    assert {"TQL-001", "TQL-002", "TQL-003"} <= ids

    review = stage.artifacts["bind_report_path"]
    assert review.exists()
    text = review.read_text(encoding="utf-8")
    assert "?road_IRI" in text and "?direction_IRI" in text


def test_a_clean_folder_produces_no_drift(tmp_path):
    """One query cannot drift against itself, and the check must not invent a
    finding from a folder with nothing to compare."""
    query = tmp_path / "only.rq"
    query.write_text(
        'prefix ex: <https://example.org/>\n'
        'CONSTRUCT { ?a_IRI a ex:Thing }\n'
        'WHERE { BIND(IRI(CONCAT("https://example.org/", ?id)) AS ?a_IRI) }\n',
        encoding="utf-8",
    )
    result = ba.analyse([str(query)])
    assert result.drift == []
    assert result.unbound == []
    assert result.is_clean
