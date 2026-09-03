"""A TARQL check can now be a query file instead of Python.

Adding a SPARQL or SHACL check has always been a file plus a registry entry
and no code: the thing being checked is a graph, and the runner understands
nothing about it. Adding a TARQL check meant editing
`sketch/bind_analysis.py`, because the thing being checked -- the text of a
`BIND` expression -- never became a graph. `tarql_visualiser` keeps each
query's CONSTRUCT template and throws the WHERE clause away, so by the time
`sketch.ttl` exists every BIND is gone.

`bind_report_to_graph` closes that by publishing the *facts* rather than more
findings. Every BIND becomes a node carrying target, expression, skeleton,
file and line; every CONSTRUCT variable becomes a node saying whether it was
bound and whether its name follows the constructed-IRI convention. A check
over those is an ordinary `.rq` in `resources/sparql/tarql/`.

What this module pins is the seam, not the one check that uses it: the facts
a query author may rely on, that those queries run against the facts graph
and nothing else, and that a finding from one is indistinguishable from a
finding from anywhere else by the time it reaches a report.
"""
import glob

import pytest
import rdflib

from ontology_suite import config, pipeline
from ontology_suite.checks import sparql_runner
from ontology_suite.checks.merge import build_unified_results
from ontology_suite.checks.registry import Registry
from ontology_suite.sketch import bind_analysis as ba

DRIFT = sorted(glob.glob(str(config.REPO_ROOT / "examples/tarql_drift/*.rq")))
FACTS = sorted(glob.glob(str(config.REPO_ROOT / "examples/tarql_facts/*.rq")))
TQ = rdflib.Namespace("https://semantechs.co.uk/ontology-quality/tarql/")


@pytest.fixture(scope="module")
def facts_graph():
    return ba.bind_report_to_graph(ba.analyse(FACTS))


# ---------------------------------------------------------------------------
# The facts a query author may rely on
# ---------------------------------------------------------------------------
def test_every_bind_carries_what_a_check_needs_to_report(facts_graph):
    """A finding has to name a place to open. Without source and line the
    facts graph would be queryable and useless."""
    binds = list(facts_graph.subjects(rdflib.RDF.type, TQ.Bind))
    assert binds, "the fixture binds two variables; neither reached the graph"
    for bind in binds:
        for predicate in (TQ.target, TQ.expression, TQ.skeleton, TQ.source, TQ.line, TQ.inQuery):
            assert facts_graph.value(bind, predicate) is not None, f"{bind} has no {predicate}"


def test_the_skeleton_is_published_because_sparql_cannot_compute_it(facts_graph):
    """The division of labour this rests on. Skeletonisation is a parse and
    stays in Python; comparing skeletons is then a GROUP BY. If the skeleton
    were not in the graph, TQL-001 could never be expressed as a query."""
    expressions = {str(o) for o in facts_graph.objects(None, TQ.expression)}
    skeletons = {str(o) for o in facts_graph.objects(None, TQ.skeleton)}
    assert expressions != skeletons, "the skeleton must differ from the raw text"
    assert any("?)" in s for s in skeletons), "variables should be blanked to ?"


def test_bound_and_unbound_variables_are_both_published(facts_graph):
    """Emitting only the gaps would make a check about what a query *does*
    bind unwritable, and half the useful questions are of that shape."""
    flags = {
        str(facts_graph.value(v, TQ.variable)): bool(facts_graph.value(v, TQ.bound))
        for v in facts_graph.subjects(rdflib.RDF.type, TQ.ConstructVariable)
    }
    assert flags, "no CONSTRUCT variables reached the graph"
    assert True in flags.values() and False in flags.values(), (
        f"the fixture has both bound and unbound CONSTRUCT variables: {flags}"
    )


def test_producesiri_is_a_parse_result_not_a_text_search():
    """The fact `TQL-004` is really about, and the reason it is computed here
    rather than asked for in the query.

    Each case below is one a `CONTAINS(?expression, "IRI(")` filter gets
    wrong, and the first draft of TQL-004 did: `MYIRI(` reads as a
    conversion, a bare string literal is missed entirely, and a nested call
    cannot be told from the outermost one. Deciding it needs the bracket
    matching the parser already did.
    """
    assert ba.produces_iri('tarql:expandPrefixedName(CONCAT("ex:_A_", ?a))') is True
    assert ba.produces_iri('IRI(CONCAT("http://x/", ?a))') is True
    assert ba.produces_iri("URI(?a)") is True

    assert ba.produces_iri('CONCAT("ex:_A_", ?a)') is False
    assert ba.produces_iri('"ex:_A_"') is False, "a bare string literal is not an IRI"

    assert ba.produces_iri("?passthrough") is None, "may already hold an IRI"
    assert ba.produces_iri("MYIRI(?a)") is None, "an unknown function is not a conversion"
    assert ba.produces_iri("CONCAT(?a, ?b) + CONCAT(?c, ?d)") is None, "no single outermost call"


def test_the_outermost_call_is_the_one_that_wraps_the_whole_expression():
    assert ba.outermost_call('tarql:expandPrefixedName(CONCAT("x", ?a))') == "tarql:expandPrefixedName"
    assert ba.outermost_call('CONCAT("x", IRI(?a))') == "CONCAT", "not the nested one"
    assert ba.outermost_call("?a") is None
    assert ba.outermost_call('CONCAT(?a) + CONCAT(?b)') is None


def test_an_undecidable_expression_publishes_no_kind(tmp_path):
    """Absent, never guessed. A check reading `tq:producesKind` must not be
    able to fire on something the parser merely did not recognise -- that is
    what lets TQL-004 be a Violation rather than a Warning."""
    query = tmp_path / "u.rq"
    query.write_text(
        "prefix ex: <https://example.org/>\n"
        "CONSTRUCT { ?a_IRI a ex:Thing ; ex:p ?b_IRI }\n"
        "WHERE { BIND(?src AS ?a_IRI) BIND(MYIRI(?src) AS ?b_IRI) }\n",
        encoding="utf-8",
    )
    graph = ba.bind_report_to_graph(ba.analyse([str(query)]))
    assert list(graph.subjects(rdflib.RDF.type, TQ.Bind)), "the binds are still published"
    assert list(graph.objects(None, TQ.producesKind)) == [], "neither expression is decidable"
    assert [r for r in _tarql_rows([str(query)]) if r.check_id == "TQL-004"] == []


def test_tql004_catches_the_bare_string_literal_the_text_search_missed(tmp_path):
    query = tmp_path / "s.rq"
    query.write_text(
        "prefix ex: <https://example.org/>\n"
        "CONSTRUCT { ?a_IRI a ex:Thing }\n"
        'WHERE { BIND("exd:_Constant_" AS ?a_IRI) }\n',
        encoding="utf-8",
    )
    rows = [r for r in _tarql_rows([str(query)]) if r.check_id == "TQL-004"]
    assert len(rows) == 1, "no CONCAT, but still a string where an IRI is wanted"


def test_value_kind_distinguishes_the_terms_a_suffix_convention_promises():
    """Why the published fact is a *kind* and not a boolean.

    `STRDT` was first classed with the string functions. That answered
    TQL-004's question correctly -- a typed literal is not an IRI either --
    and made TQL-005's question unaskable. A fact that happens to give the
    right answer to the one question asked of it is the kind that quietly
    blocks the next one.
    """
    assert ba.value_kind('tarql:expandPrefixedName(CONCAT("ex:_A_", ?a))') == "IRI"
    assert ba.value_kind("STRDT(?v, xsd:date)") == "TypedLiteral"
    assert ba.value_kind('STRLANG(?v, "en")') == "LangLiteral"
    assert ba.value_kind('CONCAT("x", ?a)') == "String"
    assert ba.value_kind("STR(?v)") == "String"
    # Bare literals carry their own kind.
    assert ba.value_kind('"2020-01-01"^^xsd:date') == "TypedLiteral"
    assert ba.value_kind('"hello"@en') == "LangLiteral"
    assert ba.value_kind('"plain"') == "String"
    assert ba.value_kind("?passthrough") is None


def test_tql005_fires_where_a_dt_variable_is_left_untyped():
    rows = [r for r in _tarql_rows(FACTS) if r.check_id == "TQL-005"]
    assert len(rows) == 1, "the fixture types one _DT variable correctly and one not"
    assert "copies_DT" in rows[0].message
    assert "String" in rows[0].message, "the message must say what it got instead"
    assert rows[0].severity == "Warning", (
        "quieter than TQL-004 on purpose: the triple loads, it is merely untyped"
    )


def test_tql005_is_silent_where_strdt_was_used():
    """?published_DT in the same fixture goes through STRDT. A check that
    fired there would be reporting the correct idiom."""
    messages = " ".join(r.message for r in _tarql_rows(FACTS) if r.check_id == "TQL-005")
    assert "published_DT" not in messages


def test_the_two_conventions_do_not_report_each_other():
    """`_IRI` and `_DT` promise different terms, and each check must mind its
    own: a `_DT` variable producing a string is not a TQL-004 finding, and an
    `_IRI` variable is not a TQL-005 one."""
    by_check = {}
    for row in _tarql_rows(FACTS):
        by_check.setdefault(row.check_id, []).append(row.message)
    assert all("_DT" not in m for m in by_check.get("TQL-004", []))
    assert all("_IRI" not in m for m in by_check.get("TQL-005", []))


def test_node_iris_are_stable_across_runs():
    """Derived from basename and line, so a finding's focus node is the same
    string on every run -- a report diffed between two runs must not churn."""
    first = ba.bind_report_to_graph(ba.analyse(FACTS))
    second = ba.bind_report_to_graph(ba.analyse(FACTS))
    assert set(first.subjects()) == set(second.subjects())


def test_a_query_with_no_binds_still_produces_its_query_node(tmp_path):
    query = tmp_path / "empty.rq"
    query.write_text(
        "prefix ex: <https://example.org/>\n"
        "CONSTRUCT { ?a a ex:Thing } WHERE { ?a a ex:Thing }\n",
        encoding="utf-8",
    )
    graph = ba.bind_report_to_graph(ba.analyse([str(query)]))
    assert list(graph.subjects(rdflib.RDF.type, TQ.Query)), "the query itself must be a node"
    assert not list(graph.subjects(rdflib.RDF.type, TQ.Bind))


# ---------------------------------------------------------------------------
# The worked example: TQL-004 as a query file
# ---------------------------------------------------------------------------
def _tarql_rows(paths):
    graph = ba.bind_report_to_graph(ba.analyse(paths))
    results, outcomes = sparql_runner.run_sparql_checks(graph, config.DEFAULT_SPARQL_DIR / "tarql")
    assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
    return build_unified_results(rdflib.Graph(), results, Registry.load(config.DEFAULT_REGISTRY_PATH))


def test_tql004_fires_on_the_unconverted_concat():
    rows = [r for r in _tarql_rows(FACTS) if r.check_id == "TQL-004"]
    assert len(rows) == 1
    row = rows[0]
    assert row.severity == "Violation"
    assert "holder_IRI" in row.message

    # The line is derived from the fixture rather than written here. Pinning
    # a literal number tests the fixture's layout, not the check, and breaks
    # every time the fixture grows a comment -- which it just did.
    fixture = (config.REPO_ROOT / "examples/tarql_facts/catalogue_to_rdf.rq").read_text(
        encoding="utf-8"
    ).splitlines()
    expected = next(
        n for n, line in enumerate(fixture, 1) if "AS ?holder_IRI)" in line
    )
    assert f"catalogue_to_rdf.rq:{expected}" in row.message, (
        "the message must name the place to open"
    )


def test_tql004_does_not_fire_where_the_string_is_converted():
    """Both fixture files in examples/tarql_drift wrap their CONCATs. A check
    that fired there would be reporting the correct idiom as a defect."""
    assert [r for r in _tarql_rows(DRIFT) if r.check_id == "TQL-004"] == []


def test_a_bare_passthrough_is_left_alone(tmp_path):
    """Deliberately out of scope: the value may already be an IRI from an
    earlier BIND, and nothing in the query text says otherwise. Reporting it
    would be guessing."""
    query = tmp_path / "p.rq"
    query.write_text(
        "prefix ex: <https://example.org/>\n"
        "CONSTRUCT { ?b_IRI a ex:Thing }\n"
        "WHERE { BIND(?a_IRI AS ?b_IRI) }\n",
        encoding="utf-8",
    )
    assert [r for r in _tarql_rows([str(query)]) if r.check_id == "TQL-004"] == []


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_tarql_queries_are_held_back_from_the_general_sweep():
    """They read a vocabulary an ontology never contains. Running them
    everywhere would cost a parse and match nothing -- which is exactly what
    a check that has quietly broken also does, so the two must not look
    alike."""
    general = sparql_runner.discover_queries(config.DEFAULT_SPARQL_DIR)
    everything = sparql_runner.discover_queries(
        config.DEFAULT_SPARQL_DIR, include_subject_specific=True
    )
    held_back = {p.name for p in set(everything) - set(general)}
    assert held_back == {"TQL-004.rq", "TQL-005.rq"}
    assert not {"TQL-004", "TQL-005"} & {p.stem for p in general}


def test_the_sketch_stage_runs_them_and_writes_the_facts(tmp_path):
    stage = pipeline.run_sketch_stage(
        str(config.REPO_ROOT / "examples/tarql_facts"), tmp_path, query_pattern="*.rq",
    )
    assert "TQL-004" in {r.check_id for r in stage.rows}
    facts = stage.artifacts["bind_facts_path"]
    assert facts.exists()
    reloaded = rdflib.Graph().parse(facts, format="turtle")
    assert list(reloaded.subjects(rdflib.RDF.type, TQ.Bind))


def test_a_project_can_add_its_own_check_without_forking(tmp_path):
    """The workflow docs/TESTING_TARQL.md promises: a query file and a
    registry entry, both outside this package, and no code at all.

    Worth a test rather than a paragraph, because it spans four things that
    each look fine alone -- the CLI passing `--sparql` and `--registry`
    through, the stage running the `tarql/` subdirectory of whichever tree it
    is given, `sh:sourceConstraintComponent` resolving against a registry
    this package did not ship, and the resulting row carrying that registry's
    severity and title.
    """
    import json

    checks = tmp_path / "my-checks"
    (checks / "sparql" / "tarql").mkdir(parents=True)
    (checks / "sparql" / "tarql" / "ACME-001.rq").write_text(
        "PREFIX sh: <http://www.w3.org/ns/shacl#>\n"
        "PREFIX oq: <https://semantechs.co.uk/ontology-quality/>\n"
        "PREFIX tq: <https://semantechs.co.uk/ontology-quality/tarql/>\n"
        "CONSTRUCT {\n"
        "  _:r a sh:ValidationResult ;\n"
        "    sh:resultSeverity sh:Warning ;\n"
        "    sh:focusNode ?bind ;\n"
        "    sh:sourceConstraintComponent oq:ACME-001 ;\n"
        "    sh:resultMessage ?msg .\n"
        "}\n"
        "WHERE {\n"
        "  ?bind a tq:Bind ; tq:target ?target ; tq:source ?source .\n"
        '  FILTER(STRENDS(?target, "_IRI"))\n'
        '  BIND(CONCAT("house rule: ?", ?target, " in ", ?source) AS ?msg)\n'
        "}\n",
        encoding="utf-8",
    )
    registry_data = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_data["checks"].append({
        "id": "ACME-001", "category": "tarql", "metric": "project IRI conventions",
        "default_severity": "Warning", "title": "IRI minted outside the house template",
        "description": "A project-local rule.", "remediation": "Use the agreed template.",
        "cucumber_feature": "TARQL Query Consistency",
        "cucumber_scenario": "Every minted IRI uses the agreed template",
    })
    registry_path = checks / "registry.json"
    registry_path.write_text(json.dumps(registry_data, indent=2), encoding="utf-8")

    stage = pipeline.run_sketch_stage(
        str(config.REPO_ROOT / "examples/tarql_facts"), tmp_path / "out",
        query_pattern="*.rq",
        registry=Registry.load(registry_path),
        sparql_dir=checks / "sparql",
    )

    rows = [r for r in stage.rows if r.check_id == "ACME-001"]
    assert len(rows) == 2, "the fixture binds two _IRI variables"
    assert rows[0].severity == "Warning", "severity comes from the project's own registry"
    assert rows[0].title == "IRI minted outside the house template"

    # ...and pointing --sparql elsewhere replaces the built-in tree rather
    # than adding to it, which the documentation has to state because the
    # alternative reading is the more natural one.
    assert "TQL-004" not in {r.check_id for r in stage.rows}


def test_a_query_source_finding_is_an_ordinary_row(tmp_path):
    """It has to carry the registry's category, title and remediation like
    any other finding, or the report has two kinds of row in it."""
    stage = pipeline.run_sketch_stage(
        str(config.REPO_ROOT / "examples/tarql_facts"), tmp_path, query_pattern="*.rq",
    )
    row = next(r for r in stage.rows if r.check_id == "TQL-004")
    assert row.category == "tarql"
    assert row.title and row.remediation
    assert row.severity == "Violation"
