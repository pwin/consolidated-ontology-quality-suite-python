"""Tests for ontology_suite.sketch.prefix_alignment: validates that TARQL
CONSTRUCT query files declare the same prefixes/namespaces as a given set
of ontology files, and flags the three ways they can drift apart.
"""
from ontology_suite.sketch import prefix_alignment as pa

EXAMPLE_QUERIES_DIR = "examples/queries"
EXAMPLE_ONTOLOGY = "examples/ontology/domain.ttl"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/"):
    return _write(
        tmp_path, "onto.ttl",
        f"@prefix {prefix}: <{iri}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f"{prefix}:DemoOntology a owl:Ontology .\n",
    )


def _query(tmp_path, name, prefix, iri):
    return _write(
        tmp_path, name,
        f"PREFIX {prefix}: <{iri}>\nCONSTRUCT {{ ?s a {prefix}:Thing . }} WHERE {{ BIND(?x AS ?s) }}\n",
    )


def test_matching_prefix_and_namespace_produces_no_finding(tmp_path):
    onto = _ontology(tmp_path)
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/demo/")
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto])
    assert findings == []


def test_same_prefix_different_namespace_is_namespace_mismatch(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/OTHER/")
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto])
    assert len(findings) == 1
    assert findings[0].kind == "namespace_mismatch"
    assert findings[0].prefix == "ex"
    assert findings[0].tarql_namespace == "https://example.org/OTHER/"


def test_same_namespace_different_prefix_is_prefix_name_mismatch(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "example", "https://example.org/demo/")
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto])
    assert len(findings) == 1
    assert findings[0].kind == "prefix_name_mismatch"
    assert findings[0].prefix == "example"


def test_namespace_absent_from_ontology_set_is_undeclared(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "foo", "https://totally-unknown.example/vocab/")
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto])
    assert len(findings) == 1
    assert findings[0].kind == "undeclared_namespace"
    assert findings[0].prefix == "foo"


def test_default_ignored_prefixes_are_skipped(tmp_path):
    onto = _ontology(tmp_path)
    query = _write(
        tmp_path, "q.rq",
        "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        "CONSTRUCT { ?s rdf:type xsd:string } WHERE { BIND(?x AS ?s) }\n",
    )
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto])
    assert findings == []


def test_ignore_prefixes_can_be_overridden(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "foo", "https://totally-unknown.example/vocab/")
    findings = pa.check_tarql_ontology_prefix_alignment(
        [query], [onto], ignore_prefixes=pa.DEFAULT_IGNORED_PREFIXES | {"foo"}
    )
    assert findings == []


def test_multiple_query_files_are_all_checked(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    good = _query(tmp_path, "good.rq", "ex", "https://example.org/demo/")
    bad = _query(tmp_path, "bad.rq", "ex", "https://example.org/WRONG/")
    findings = pa.check_tarql_ontology_prefix_alignment([good, bad], [onto])
    assert len(findings) == 1
    assert findings[0].tarql_file == str(bad)


def test_folder_of_queries_is_expanded(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    _query(query_dir, "q1.rq", "ex", "https://example.org/demo/")
    _query(query_dir, "q2.rq", "ex", "https://example.org/WRONG/")
    findings = pa.check_tarql_ontology_prefix_alignment([query_dir], [onto])
    assert len(findings) == 1
    assert findings[0].tarql_file.endswith("q2.rq")


def test_multiple_ontology_files_are_merged(tmp_path):
    onto_a = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    onto_dir = tmp_path / "extra"
    onto_dir.mkdir()
    onto_b = _write(
        onto_dir, "gist.ttl",
        "@prefix gist: <https://w3id.org/semanticarts/ns/ontology/gist/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "gist:Category a owl:Class .\n",
    )
    query = _query(tmp_path, "q.rq", "gist", "https://w3id.org/semanticarts/ns/ontology/gist/")
    findings = pa.check_tarql_ontology_prefix_alignment([query], [onto_a, onto_b])
    assert findings == []


def test_real_example_queries_align_with_domain_ontology():
    """examples/queries/animals.rq's `ex:` and examples/ontology/domain.ttl's
    `ex:` are the same namespace by design (see the query's own comment
    about deliberately testing CNF-002, not prefix drift) -- this is a
    real-world regression check that nothing here is spuriously flagged."""
    findings = pa.check_tarql_ontology_prefix_alignment([EXAMPLE_QUERIES_DIR], [EXAMPLE_ONTOLOGY])
    assert findings == []


def test_format_report_empty():
    assert pa.format_report([]) == "No prefix/namespace misalignments found."


def test_format_report_lists_each_finding():
    findings = [
        pa.PrefixMisalignment(
            tarql_file="q.rq", prefix="ex", tarql_namespace="https://wrong/",
            kind="namespace_mismatch", detail="something drifted",
        )
    ]
    report = pa.format_report(findings)
    assert "1 prefix/namespace misalignment" in report
    assert "[namespace_mismatch] q.rq: something drifted" in report


def test_cli_exits_zero_by_default_even_with_findings(tmp_path, capsys):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/WRONG/")
    exit_code = pa.main(["--queries", str(query), "--ontology", str(onto)])
    assert exit_code == 0
    assert "namespace_mismatch" in capsys.readouterr().out


def test_cli_fail_on_mismatch_exits_nonzero(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/WRONG/")
    exit_code = pa.main(["--queries", str(query), "--ontology", str(onto), "--fail-on-mismatch"])
    assert exit_code == 1


# --- check_undeclared_terms / combined alignment report --------------------

def _ontology_with_class(tmp_path, prefix="ex", iri="https://example.org/demo/", cls="Thing"):
    return _write(
        tmp_path, "onto.ttl",
        f"@prefix {prefix}: <{iri}> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f"{prefix}:DemoOntology a owl:Ontology .\n{prefix}:{cls} a owl:Class .\n",
    )


def test_class_declared_in_ontology_produces_no_undeclared_finding(tmp_path):
    onto = _ontology_with_class(tmp_path, cls="Thing")
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/demo/")
    findings = pa.check_undeclared_terms([query], [onto])
    assert findings == []


def test_class_used_but_not_declared_is_reported(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")  # no ex:Thing declared
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/demo/")
    findings = pa.check_undeclared_terms([query], [onto])
    assert len(findings) == 1
    assert findings[0].kind == "class"
    assert findings[0].term == "https://example.org/demo/Thing"


def test_property_used_but_not_declared_is_reported(tmp_path):
    onto = _ontology_with_class(tmp_path, cls="Thing")
    query = _write(
        tmp_path, "q.rq",
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT { ?s a ex:Thing ; ex:missingProp ?v . } WHERE { BIND(?x AS ?s) BIND(?y AS ?v) }\n",
    )
    findings = pa.check_undeclared_terms([query], [onto])
    assert len(findings) == 1
    assert findings[0].kind == "property"
    assert findings[0].term == "https://example.org/demo/missingProp"


def test_real_example_queries_have_one_known_undeclared_property():
    """examples/queries/animals.rq's own comment says `ex:species` is
    *deliberately* never declared in examples/ontology/domain.ttl, precisely
    to exercise this check (and the pipeline's CNF-002) -- so exactly that
    one undeclared property, and nothing else, should be reported."""
    findings = pa.check_undeclared_terms([EXAMPLE_QUERIES_DIR], [EXAMPLE_ONTOLOGY])
    assert len(findings) == 1
    assert findings[0].kind == "property"
    assert findings[0].term == "https://example.org/demo/species"


def test_combined_alignment_report_includes_both_kinds(tmp_path):
    onto = _ontology(tmp_path, prefix="ex", iri="https://example.org/demo/")  # no ex:Thing declared
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/WRONG/")  # also a prefix mismatch
    report = pa.check_tarql_ontology_alignment([query], [onto])
    assert not report.is_clean
    assert len(report.prefix_misalignments) == 1
    assert len(report.undeclared_terms) == 1
    formatted = pa.format_alignment_report(report)
    assert "namespace_mismatch" in formatted
    assert "undeclared_class" in formatted


def test_combined_alignment_report_clean_when_nothing_to_report(tmp_path):
    onto = _ontology_with_class(tmp_path, cls="Thing")
    query = _query(tmp_path, "q.rq", "ex", "https://example.org/demo/")
    report = pa.check_tarql_ontology_alignment([query], [onto])
    assert report.is_clean
    assert pa.format_alignment_report(report) == (
        "No prefix/namespace misalignments or undeclared classes/properties found."
    )
