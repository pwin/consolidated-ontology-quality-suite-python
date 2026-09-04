"""Defects recorded in the SemOps manual's "rough edges" register.

Each of these was found by someone writing documentation against the tools
rather than by a test, which is the useful thing about them: they are the
failures that survive a green suite. Each cost time, each was reproducible,
and each is pinned here so the register can record it as closed rather than
carrying it forward.

The register's own lesson is worth restating, since this module exists to
serve it: *re-verify the gaps list against the tools you actually have.* A
limitation copied forward from an old document is indistinguishable from a
current one, right up until someone works around something that was fixed.
"""
import json

import pytest

from ontology_suite import config
from ontology_suite.docgen import extract_ontology_data as ed
from ontology_suite.docgen.turtle_parser import Literal, parse_turtle
from ontology_suite.sketch import tarql_visualiser

ACME = config.REPO_ROOT / "examples" / "acme_robotics"


# ---------------------------------------------------------------------------
# "`sketch --queries` requires a directory, not a file"
# ---------------------------------------------------------------------------
# The flag reads like it accepts a file, this module's own description has
# always said it does, and the error named the file it had been given and
# said it could not find any files in it.
def test_a_single_query_file_is_accepted(tmp_path):
    query = tmp_path / "one.rq"
    query.write_text(
        "prefix ex: <https://example.org/>\n"
        "CONSTRUCT { ?a_IRI a ex:Thing . } WHERE { BIND(IRI('x') AS ?a_IRI) }\n",
        encoding="utf-8",
    )
    used = tarql_visualiser.visualise_folder(str(query), str(tmp_path / "sketch.ttl"))
    assert used == [str(query)]


def test_a_folder_still_works(tmp_path):
    for name in ("a.rq", "b.rq"):
        (tmp_path / name).write_text(
            "prefix ex: <https://example.org/>\n"
            "CONSTRUCT { ?a_IRI a ex:Thing . } WHERE { BIND(IRI('x') AS ?a_IRI) }\n",
            encoding="utf-8",
        )
    used = tarql_visualiser.visualise_folder(str(tmp_path), str(tmp_path / "out.ttl"))
    assert len(used) == 2


def test_a_path_that_is_neither_says_which_problem_it_is(tmp_path):
    """The original message named the file and said no files matched in it,
    which reads like a pattern problem when it is a path problem."""
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError) as excinfo:
        tarql_visualiser.visualise_folder(str(missing), str(tmp_path / "out.ttl"))
    assert "is not a directory" in str(excinfo.value)


# ---------------------------------------------------------------------------
# "`--apply-repairs` rewrites comments as well as code"
# ---------------------------------------------------------------------------
# The rename repair was a textual substitution across the whole file, so a
# comment describing the rename was itself renamed -- turning a true remark
# into a false one.
PREFIXES = {"acme": "https://acme.example.org/ns/"}
OLD_IRI = "https://acme.example.org/ns/Engineer"
NEW_IRI = "https://acme.example.org/ns/SoftwareEngineer"


def _rename(text):
    from ontology_suite.repair.text_edit import replace_term

    return replace_term(text, OLD_IRI, NEW_IRI, PREFIXES)


def test_a_comment_describing_the_rename_survives_it():
    comment = "# every row is typed acme:Engineer, which v2.0.0 renames to acme:SoftwareEngineer\n"
    assert _rename(comment) == comment


def test_a_bracketed_iri_in_a_comment_is_left_alone():
    comment = f"# see <{OLD_IRI}> for the old name\n"
    assert _rename(comment) == comment


def test_the_code_is_still_renamed():
    """The fix must not turn into 'change nothing'."""
    assert _rename("?p a acme:Engineer .") == "?p a acme:SoftwareEngineer ."
    assert _rename(f"?p a <{OLD_IRI}> .") == "?p a acme:SoftwareEngineer ."


def test_a_string_literal_is_still_renamed():
    """Deliberate, and the reason this is not 'skip comments and strings': a
    TARQL IRI template is built out of literals, so a rename usually must
    reach inside them."""
    assert _rename('BIND("acme:Engineer" AS ?t)') == 'BIND("acme:SoftwareEngineer" AS ?t)'


def test_code_is_renamed_even_when_a_comment_mentions_it_first():
    """The mask preserves offsets, so a comment earlier in the file must not
    shift the positions of the real matches after it."""
    text = (
        "# renaming acme:Engineer here\n"
        "?p a acme:Engineer .\n"
    )
    assert _rename(text) == (
        "# renaming acme:Engineer here\n"
        "?p a acme:SoftwareEngineer .\n"
    )


# ---------------------------------------------------------------------------
# "`--own-namespace` fails silently on a near-miss"
# ---------------------------------------------------------------------------
# It is a literal IRI-prefix string match, so a wrong scheme, host or trailing
# separator produced "Findings: 0 total" -- indistinguishable from a clean
# run, which is the worst thing a gate can be.
def _row(focus):
    from ontology_suite.checks.merge import ResultRow

    return ResultRow(
        check_id="STR-001", category="structural", title="t", severity="Warning",
        focus_node=focus, path=None, value=None, message="m", remediation=None,
        sources=["sparql"],
    )


def test_a_namespace_matching_nothing_warns():
    from ontology_suite import cli

    warnings = []
    rows = [_row("https://acme.example.org/ns/Robot")]
    assert cli._filter_own_namespace(rows, "http://example.org/acme#", warnings) == []
    assert len(warnings) == 1
    assert "matched none of the 1 findings" in warnings[0]
    # The usual cause is a near miss, and the answer is nearly always in the
    # list of namespaces that were actually present.
    assert "https://acme.example.org/ns/" in warnings[0]


def test_a_namespace_that_matches_does_not_warn():
    from ontology_suite import cli

    warnings = []
    rows = [_row("https://acme.example.org/ns/Robot")]
    assert len(cli._filter_own_namespace(rows, "https://acme.example.org/ns/", warnings)) == 1
    assert warnings == []


def test_a_genuinely_clean_run_does_not_warn():
    """Nothing matched because there was nothing. Warning here would train
    people to ignore the message, which costs more than it saves."""
    from ontology_suite import cli

    warnings = []
    assert cli._filter_own_namespace([], "http://example.org/acme#", warnings) == []
    assert warnings == []


def test_blank_nodes_are_not_offered_as_namespaces():
    """`_:0_b56` sorts to the front, so the first thing the hint showed was
    the one entry that could never be the answer."""
    from ontology_suite import cli

    warnings = []
    cli._filter_own_namespace(
        [_row("_:0_b56"), _row("https://acme.example.org/ns/Robot")],
        "http://example.org/acme#",
        warnings,
    )
    assert "_:" not in warnings[0]


# ---------------------------------------------------------------------------
# "`docgen --ref` does not sniff serialisation format"
# ---------------------------------------------------------------------------
def test_a_reference_vocabulary_in_rdf_xml_parses():
    """FOAF publishes RDF/XML. Handing it to the Turtle parser failed on the
    file's XML comment header, reporting a Turtle syntax error in a file
    containing no Turtle."""
    path = ACME / "reference_vocab" / "foaf.rdf"
    triples, prefixes = parse_turtle(ed.read_normalized(str(path)), source=str(path))
    assert triples, "no triples parsed out of the RDF/XML reference"


def test_turtle_is_still_the_assumption_without_a_source():
    triples, _ = parse_turtle("@prefix ex: <https://example.org/> .\nex:a a ex:Thing .\n")
    assert triples


# ---------------------------------------------------------------------------
# "`docgen` external-term resolution did not engage"
# ---------------------------------------------------------------------------
# Reported as observed rather than diagnosed. The cause: resolution compared
# the CURIE the *reference* would write against the CURIE the *ontology*
# wrote, so it depended on two independent files choosing the same prefix.
def test_a_term_resolves_even_though_the_files_disagree_about_the_prefix():
    """W3C's org.ttl declares `http://www.w3.org/ns/org#` twice -- once as
    `org:` and once as the default `:` -- and the prefix map is built by
    inverting {prefix: ns}, so the later declaration wins and every term
    renders `:OrganizationalUnit`. The ontology under test calls the same
    term `org:OrganizationalUnit`."""
    ref = ACME / "reference_vocab" / "org.ttl"
    triples, prefixes = parse_turtle(ed.read_normalized(str(ref)), source=str(ref))
    graph = ed.Graph(triples)

    # The precondition: the two files really do disagree about the prefix.
    ns_map = ed.build_ns_maps(prefixes)
    assert ns_map.get("<http://www.w3.org/ns/org#>") != "org", (
        "fixture no longer exercises the aliasing that caused this"
    )

    info = ed.get_definition_from_reference(graph, "http://www.w3.org/ns/org#OrganizationalUnit")
    assert info is not None, "the term is defined in the reference and must resolve"
    assert info["kind"] == "Class"
    assert info["definition"]


def test_an_absent_term_still_resolves_to_nothing():
    """Matching on IRI must not turn into matching on anything."""
    ref = ACME / "reference_vocab" / "org.ttl"
    triples, _ = parse_turtle(ed.read_normalized(str(ref)), source=str(ref))
    graph = ed.Graph(triples)
    assert ed.get_definition_from_reference(graph, "https://example.org/NotThere") is None


# ---------------------------------------------------------------------------
# The defect the fix above uncovered: a nondeterministic definition
# ---------------------------------------------------------------------------
# Once resolution worked, org.ttl's multilingual rdfs:comment surfaced. Five
# identical runs produced the definition in three different languages,
# because `graph.objects` order follows Python's per-process string hashing.
def test_the_document_language_is_preferred():
    values = [
        Literal("une definition", lang="fr"),
        Literal("a definition", lang="en"),
        Literal("una definizione", lang="it"),
    ]
    assert ed.preferred_literal(values).value == "a definition"


def test_an_untagged_literal_is_next_best():
    """What a single-language ontology writes, and what the local ontology
    path relies on."""
    values = [Literal("une definition", lang="fr"), Literal("plain")]
    assert ed.preferred_literal(values).value == "plain"


def test_a_vocabulary_offering_neither_still_yields_something_stable():
    """Falling back to nothing would trade a wrong definition for a blank
    one. Falling back to an arbitrary one would keep the nondeterminism."""
    values = [Literal("zzz", lang="fr"), Literal("aaa", lang="it")]
    assert ed.preferred_literal(values).value == "aaa"
    assert ed.preferred_literal(list(reversed(values))).value == "aaa"


def test_no_values_is_not_an_error():
    assert ed.preferred_literal([]) is None


def test_docgen_resolves_and_stays_in_english_end_to_end(tmp_path):
    from ontology_suite import pipeline

    seen = set()
    for run in range(3):
        out = tmp_path / f"run{run}"
        pipeline.run_docgen_stage(
            ontology_path=str(ACME / "acme-org-v1.ttl"),
            out_dir=out,
            ref_paths=[str(ACME / "reference_vocab" / "org.ttl")],
        )
        data = json.loads((out / "ontology_doc_data.json").read_text(encoding="utf-8"))
        entry = next(t for t in data["externalReuse"] if t["id"] == "org:OrganizationalUnit")
        assert entry["kind"] == "Class", "the term must resolve, not be reported Unknown"
        seen.add(entry["definition"])
    assert len(seen) == 1, f"the definition varied between identical runs: {seen}"
    assert "Organization" in next(iter(seen)), "the English definition is expected"
