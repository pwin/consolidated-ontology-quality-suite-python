"""`findings.txt`: the finding set arranged for a person, and the locator
that gives it positions.

Two properties carry this module. The output must be **complete** -- the same
finding set as `full_results.csv`, every row present exactly once, nothing
capped or sampled -- and it must be **unambiguous**, which is a different
thing: no two lines may read identically unless they describe the same
finding. A report that looks like a summary and quietly drops rows is worse
than no report, and five identical lines that are five different findings are
worse still, because the reader cannot tell which failure mode they are
looking at.

The rest is duplication control, which is what this arrangement is for. The
same source line is quoted once however many checks fired on it, and each
check's remediation is printed once rather than once per finding.
"""
import pytest

from ontology_suite.checks import locate
from ontology_suite.checks.merge import ResultRow
from ontology_suite.report import findings_text as ft

ONTOLOGY = """\
@prefix acme: <https://acme.example.org/ns/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# acme:Ghost is mentioned here but never declared.

acme:Robot a owl:Class ;
    rdfs:label "Robot" .

acme:Arm a owl:Class ;
    rdfs:subClassOf acme:Robot .
"""


def row(**kw):
    base = dict(
        check_id="QUA-001", category="quality", title="Missing label",
        severity="Warning", focus_node="https://acme.example.org/ns/Robot",
        path=None, value=None, message="has no label", remediation="Add a label.",
        sources=["sparql"], source_file=None, line=None,
    )
    base.update(kw)
    return ResultRow(**base)


# ---------------------------------------------------------------------------
# the locator
# ---------------------------------------------------------------------------
def test_a_declared_term_is_found_by_its_curie():
    assert locate.find_declaring_line(ONTOLOGY, "https://acme.example.org/ns/Robot") == 7


def test_the_second_declaration_is_found_too():
    assert locate.find_declaring_line(ONTOLOGY, "https://acme.example.org/ns/Arm") == 10


def test_a_term_only_mentioned_in_a_comment_is_not_located():
    """The off-by-one this would otherwise cause is the nastiest kind: a term
    is routinely discussed in a comment right above where it is defined, so
    matching the remark reports a line one or two above the real one, which
    looks plausible enough never to be questioned."""
    assert locate.find_declaring_line(ONTOLOGY, "https://acme.example.org/ns/Ghost") is None


def test_a_term_the_file_never_mentions_is_not_located():
    assert locate.find_declaring_line(ONTOLOGY, "http://xmlns.com/foaf/0.1/Person") is None


def test_a_blank_node_is_never_located():
    """`_:b0` is minted fresh on each parse and names nothing in the text."""
    assert locate.find_declaring_line(ONTOLOGY, "_:b0") is None


def test_a_full_iri_form_is_found():
    text = "<https://acme.example.org/ns/Robot> a <http://www.w3.org/2002/07/owl#Class> .\n"
    assert locate.find_declaring_line(text, "https://acme.example.org/ns/Robot") == 1


def test_a_term_used_only_as_an_object_is_not_located():
    """Declining is the whole point of the module. The alternative -- the
    extension's fallback to line 0 -- reads as a real answer and sends the
    reader to the top of the file."""
    text = "@prefix acme: <https://acme.example.org/ns/> .\n:x a acme:Robot .\n"
    assert locate.find_declaring_line(text, "https://acme.example.org/ns/Robot") is None


def test_every_prefix_bound_to_the_namespace_is_tried():
    """One namespace under several prefixes is normal and it is what made
    docgen's external-term resolution fail silently: W3C's org.ttl binds
    `http://www.w3.org/ns/org#` as both `org:` and the default `:`."""
    text = (
        "@prefix org: <http://www.w3.org/ns/org#> .\n"
        "@prefix : <http://www.w3.org/ns/org#> .\n"
        ":OrganizationalUnit a owl:Class .\n"
    )
    assert locate.find_declaring_line(text, "http://www.w3.org/ns/org#OrganizationalUnit") == 3


def test_a_commented_out_prefix_does_not_bind():
    text = "# @prefix acme: <https://acme.example.org/ns/> .\nacme:Robot a owl:Class .\n"
    assert locate.find_declaring_line(text, "https://acme.example.org/ns/Robot") is None


def test_locate_rows_leaves_an_exact_position_alone(tmp_path):
    """A TARQL finding's position came from parsing the query text. Replacing
    it with a text search would trade an exact answer for a guess."""
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    exact = row(source_file="query.rq", line=99)
    locate.locate_rows([exact], path)
    assert (exact.source_file, exact.line) == ("query.rq", 99)


def test_locate_rows_reports_how_many_it_placed(tmp_path):
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    rows = [row(), row(focus_node="http://xmlns.com/foaf/0.1/Person")]
    assert locate.locate_rows(rows, path) == 1
    assert rows[0].line == 7
    assert rows[1].line is None


def test_an_unreadable_source_places_nothing_and_does_not_raise(tmp_path):
    rows = [row()]
    assert locate.locate_rows(rows, tmp_path / "missing.ttl") == 0
    assert rows[0].line is None


# ---------------------------------------------------------------------------
# completeness
# ---------------------------------------------------------------------------
def test_every_finding_appears_exactly_once(tmp_path):
    """The property that makes this a report rather than a sample."""
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    rows = [
        row(check_id="QUA-001", line=7, source_file=str(path)),
        row(check_id="QUA-004", line=7, source_file=str(path), message="no prefLabel"),
        row(check_id="STR-002", focus_node="http://xmlns.com/foaf/0.1/Person",
            message="undeclared", severity="Violation"),
    ]
    text = ft.render(rows)
    assert text.startswith("3 finding(s): 1 Violation, 2 Warning")
    assert sum(1 for line in text.splitlines() if "QUA-001" in line and "Warning" in line) >= 1
    for marker in ("QUA-001", "QUA-004", "STR-002"):
        assert marker in text


def test_the_header_count_matches_the_rows():
    rows = [row() for _ in range(7)]
    for index, r in enumerate(rows):
        r.focus_node = f"https://acme.example.org/ns/T{index}"
    assert ft.render(rows).startswith("7 finding(s): 7 Warning")


def test_no_findings_says_so():
    assert ft.render([]) == "No findings.\n"


def test_an_unplaced_finding_is_listed_not_dropped():
    """For an ontology built mostly of imports these may be most of the run.
    Dropping them to keep the output tidy would make it a partial record that
    looks complete."""
    text = ft.render([row(focus_node="http://xmlns.com/foaf/0.1/Person")])
    assert "not located: 1 finding(s)" in text
    assert "http://xmlns.com/foaf/0.1/Person" in text


# ---------------------------------------------------------------------------
# no two lines may read the same unless they mean the same
# ---------------------------------------------------------------------------
def test_findings_differing_only_in_a_hidden_field_are_told_apart():
    """Five STR-002 findings on this repo's worked example shared a focus node
    and a message, differing only in `path` -- the undeclared predicate, which
    the check's own message does not name. They printed as five identical
    lines."""
    rows = [
        row(check_id="STR-002", severity="Violation", focus_node="http://www.w3.org/ns/org#",
            path=f"http://purl.org/dc/terms/{term}", message="A predicate is never declared")
        for term in ("created", "modified", "title")
    ]
    body = [line for line in ft.render(rows).splitlines() if "purl.org" in line]
    assert len(body) == 3
    assert len(set(body)) == 3, "three different findings printed as fewer distinct lines"


def test_a_lone_finding_is_not_cluttered_with_fields_it_does_not_need():
    """The other half of the rule. Shown unconditionally, a located TARQL
    finding carried `(path .../tarql/expression, value CONCAT(...))` -- an
    internal predicate nobody asked about and an expression the quoted source
    line was already showing."""
    text = ft.render([row(path="https://semantechs.co.uk/ontology-quality/tarql/expression",
                          value="CONCAT('a', ?b)")])
    assert "tarql/expression" not in text


# ---------------------------------------------------------------------------
# duplication control
# ---------------------------------------------------------------------------
def test_one_source_line_is_quoted_once_however_many_checks_fired(tmp_path):
    """15 of 25 located findings on the worked example shared a line with
    another, so a check-first layout quoted the same Turtle repeatedly."""
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    rows = [
        row(check_id=cid, line=7, source_file=str(path), message=f"{cid} says so")
        for cid in ("QUA-001", "QUA-004", "QUA-009")
    ]
    text = ft.render(rows)
    assert text.count("acme:Robot a owl:Class ;") == 1
    for cid in ("QUA-001", "QUA-004", "QUA-009"):
        assert cid in text


def test_remediation_is_printed_once_per_check_not_once_per_finding(tmp_path):
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    rows = [
        row(line=7, source_file=str(path), focus_node=f"https://acme.example.org/ns/T{i}")
        for i in range(20)
    ]
    assert ft.render(rows).count("Add a label.") == 1


def test_nearby_positions_share_one_extract(tmp_path):
    """Two findings three lines apart have overlapping context windows. Left
    unmerged the reader sees the same Turtle twice with a different line
    marked -- the duplication moved rather than removed."""
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    text = ft.render([
        row(line=7, source_file=str(path)),
        row(check_id="QUA-004", line=10, source_file=str(path), message="other"),
    ])
    assert text.count("acme:Robot a owl:Class ;") == 1
    assert text.count("acme:Arm a owl:Class ;") == 1


def test_a_merged_block_still_says_which_line_each_finding_is_on(tmp_path):
    """Four classes flagged in one block produced four identical generic
    messages with nothing to tie each back to its subject."""
    path = tmp_path / "o.ttl"
    path.write_text(ONTOLOGY, encoding="utf-8")
    text = ft.render([
        row(line=7, source_file=str(path), message="Term must have a prefLabel."),
        row(line=10, source_file=str(path), focus_node="https://acme.example.org/ns/Arm",
            message="Term must have a prefLabel."),
    ])
    findings = [ln for ln in text.splitlines() if "Term must have a prefLabel." in ln]
    assert len(findings) == 2
    assert len(set(findings)) == 2, "identical lines for findings on different lines"
    assert any(ln.strip().startswith("7 ") for ln in findings)
    assert any(ln.strip().startswith("10 ") for ln in findings)


def test_clusters_merge_only_when_they_touch():
    assert ft._clusters([10], 100) == [(8, 12)]
    assert ft._clusters([10, 13], 100) == [(8, 15)]
    assert ft._clusters([10, 40], 100) == [(8, 12), (38, 42)]


def test_clusters_stay_inside_the_file():
    assert ft._clusters([1], 3) == [(1, 3)]
    assert ft._clusters([3], 3) == [(1, 3)]


def test_a_source_that_vanished_keeps_the_finding_and_its_position(tmp_path):
    """An extract is a courtesy. Losing it must not lose the finding."""
    text = ft.render([row(line=7, source_file=str(tmp_path / "gone.ttl"))])
    assert "no longer readable" in text
    assert "QUA-001" in text
    assert "7" in text


def test_the_fix_section_lists_every_check_that_fired():
    """It doubles as the check-centric index the by-place layout does not
    give, so a check missing from it is a check with no entry point."""
    rows = [
        row(check_id="QUA-001"),
        row(check_id="STR-002", focus_node="http://xmlns.com/foaf/0.1/Person",
            remediation="Declare it."),
    ]
    tail = ft.render(rows).split("how to fix")[1]
    assert "QUA-001" in tail and "STR-002" in tail
    assert "Add a label." in tail and "Declare it." in tail


# ---------------------------------------------------------------------------
# the "by question" index
# ---------------------------------------------------------------------------
# A theme map groups findings by the question a project is actually asking --
# "IRI construction pattern not updated following a model change" rather than
# "CNF-002". It is an input rather than registry data because the grouping is
# a property of the project: the survey this was built against maps 38
# questions onto 45 checks across 48 pairs, several of its questions are
# answered by things carrying no registry id, and a different project asking
# different questions would map them differently.
THEMES = {
    "Missing preferred label": ["QUA-004", "QUA-009"],
    "Multiple preferred labels for the same language": ["QUA-009"],
}


def test_the_index_groups_checks_under_their_question():
    rows = [row(check_id="QUA-004"), row(check_id="QUA-009"), row(check_id="QUA-009")]
    for index, r in enumerate(rows):
        r.focus_node = f"https://acme.example.org/ns/T{index}"
    text = ft.render(rows, themes=THEMES)
    assert "Missing preferred label  (3)" in text
    assert "QUA-004 x1, QUA-009 x2" in text


def test_a_check_answering_two_questions_is_counted_under_both():
    """Many-to-many is the normal case, not a defect in the survey."""
    text = ft.render([row(check_id="QUA-009")], themes=THEMES)
    assert "Missing preferred label  (1)" in text
    assert "Multiple preferred labels for the same language  (1)" in text


def test_overlapping_counts_are_declared():
    """A reader who adds them up and misses the total will assume one is
    wrong, so the output says why they do not sum."""
    assert "overlap and do not sum" in ft.render([row(check_id="QUA-009")], themes=THEMES)


def test_no_overlap_means_no_overlap_note():
    text = ft.render([row(check_id="QUA-004")], themes={"One question": ["QUA-004"]})
    assert "overlap and do not sum" not in text


def test_a_check_no_question_covers_is_named_not_hidden():
    """"It found nothing" and "this survey asks nothing about it" must be
    distinguishable, because only one of them means there is nothing to do."""
    text = ft.render([row(check_id="LOG-001", severity="Violation")], themes=THEMES)
    assert "not mapped to a question  (1)" in text
    assert "LOG-001" in text


def test_a_question_with_no_findings_is_not_listed():
    """The index is what this run has something to say about. Listing every
    question in the survey with a zero against it buries the ones that fired."""
    text = ft.render([row(check_id="QUA-004")], themes=THEMES)
    assert "Multiple preferred labels" not in text


def test_the_index_does_not_restate_the_findings():
    """An index, not a fourth listing. The index names check ids and counts;
    the finding itself is printed once, under its place or its check."""
    rows = [row(check_id="QUA-004", message="a distinctive message")]
    text = ft.render(rows, themes=THEMES)
    assert text.count("a distinctive message") == 1
    assert text.count("QUA-004") == 3, "index, not-located heading, and how-to-fix"


def test_themes_are_optional():
    assert "by question" not in ft.render([row()])


def test_themes_load_from_json(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('{"A question": ["STR-002", "TQL-001"]}', encoding="utf-8")
    assert ft.load_themes(path) == {"A question": ["STR-002", "TQL-001"]}


def test_themes_load_from_csv_with_or_without_a_header(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("check_id,theme\nSTR-002,A question\nTQL-001,A question\n", encoding="utf-8")
    assert ft.load_themes(path) == {"A question": ["STR-002", "TQL-001"]}

    bare = tmp_path / "bare.csv"
    bare.write_text("STR-002,A question\n", encoding="utf-8")
    assert ft.load_themes(bare) == {"A question": ["STR-002"]}


def test_a_csv_header_is_not_mistaken_for_a_check(tmp_path):
    """Tolerated by shape rather than by being told: a check id looks like
    `ABC-123` and "check_id" does not."""
    path = tmp_path / "t.csv"
    path.write_text("check_id,theme\nSTR-002,A question\n", encoding="utf-8")
    assert "check_id" not in {c for ids in ft.load_themes(path).values() for c in ids}


def test_a_template_message_is_said_once_for_the_whole_group():
    """"<X> has no rdfs:label" under 20 findings is one sentence printed 20
    times with one word changing -- and that word is the line beneath it."""
    rows = [
        row(focus_node=f"https://acme.example.org/ns/T{i}",
            message=f"https://acme.example.org/ns/T{i} has no rdfs:label")
        for i in range(20)
    ]
    text = ft.render(rows)
    assert text.count("has no rdfs:label") == 1
    for i in range(20):
        assert f"ns/T{i}" in text, "every focus node must still be listed"


def test_a_message_carrying_more_than_the_fields_is_kept_per_finding():
    """The completeness half: the saving is in not repeating what is already
    visible, never in dropping what is not."""
    rows = [
        row(focus_node="https://acme.example.org/ns/A", message="A is wrong because of Monday"),
        row(focus_node="https://acme.example.org/ns/B", message="B is wrong because of Tuesday"),
    ]
    text = ft.render(rows)
    assert "Monday" in text and "Tuesday" in text
