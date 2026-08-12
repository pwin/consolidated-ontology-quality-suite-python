"""Covers the `--own-namespace` filter (`cli._filter_own_namespace`, wired
into `checks`/`data`/`run`) added in response to a real usability gap: an
ontology that imports (or otherwise mixes in) a vocabulary the caller
doesn't own produces a report mixing "my findings" with "the imported
vocabulary's own pre-existing findings", with no built-in way to see just
the former short of grepping full_results.csv by hand. This is a
report-layer filter -- it must not change *what* gets checked (imports stay
resolved, reasoning is unaffected), only what's shown/written.
"""
from ontology_suite import cli

EX_OWN = "https://example.org/acme/"
EX_OTHER = "https://example.org/ext/"


def _write_mixed_namespace_ontology(tmp_path):
    """Two classes, neither with an rdfs:label -- both trigger QUA-001
    ("Class or property missing rdfs:label"), one in each namespace, so the
    filter's effect is directly observable in full_results.csv."""
    path = tmp_path / "mixed.ttl"
    path.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f"@prefix acme: <{EX_OWN}> .\n"
        f"@prefix ext: <{EX_OTHER}> .\n"
        "acme:Widget a owl:Class .\n"
        "ext:Category a owl:Class .\n",
        encoding="utf-8",
    )
    return path


def test_checks_own_namespace_filters_out_imported_vocabulary_findings(tmp_path):
    ontology_path = _write_mixed_namespace_ontology(tmp_path)

    unfiltered_dir = tmp_path / "out-unfiltered"
    cli.main(["checks", "--ontology", str(ontology_path), "--out-dir", str(unfiltered_dir), "--fail-on", "never"])
    unfiltered_csv = (unfiltered_dir / "full_results.csv").read_text(encoding="utf-8")
    assert "acme:Widget" not in unfiltered_csv  # focus_node is written as a full IRI, not a prefixed name
    assert EX_OWN + "Widget" in unfiltered_csv
    assert EX_OTHER + "Category" in unfiltered_csv

    filtered_dir = tmp_path / "out-filtered"
    cli.main([
        "checks", "--ontology", str(ontology_path), "--out-dir", str(filtered_dir),
        "--fail-on", "never", "--own-namespace", EX_OWN,
    ])
    filtered_csv = (filtered_dir / "full_results.csv").read_text(encoding="utf-8")
    assert EX_OWN + "Widget" in filtered_csv
    assert EX_OTHER + "Category" not in filtered_csv


def test_data_own_namespace_filters_out_imported_vocabulary_findings(tmp_path):
    ontology_path = _write_mixed_namespace_ontology(tmp_path)

    filtered_dir = tmp_path / "out-data-filtered"
    cli.main([
        "data", str(ontology_path), "--ontology", str(ontology_path),
        "--out-dir", str(filtered_dir), "--own-namespace", EX_OWN,
    ])
    filtered_csv = (filtered_dir / "full_results.csv").read_text(encoding="utf-8")
    assert EX_OTHER + "Category" not in filtered_csv


def test_own_namespace_absent_is_a_no_op():
    from ontology_suite.checks.merge import ResultRow

    rows = [
        ResultRow(
            check_id="QUA-001", category="quality", title=None, severity="Warning",
            focus_node=EX_OWN + "Widget", path=None, value=None, message="", remediation=None,
        )
    ]
    assert cli._filter_own_namespace(rows, None) is rows


def test_own_namespace_prefix_matching_is_exact_string_prefix():
    from ontology_suite.checks.merge import ResultRow

    rows = [
        ResultRow(
            check_id="QUA-001", category="quality", title=None, severity="Warning",
            focus_node=EX_OWN + "Widget", path=None, value=None, message="", remediation=None,
        ),
        ResultRow(
            check_id="QUA-001", category="quality", title=None, severity="Warning",
            focus_node="https://example.org/acme-other/Thing", path=None, value=None, message="", remediation=None,
        ),
    ]
    kept = cli._filter_own_namespace(rows, EX_OWN)
    assert len(kept) == 1
    assert kept[0].focus_node == EX_OWN + "Widget"
