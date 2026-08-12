"""Covers report.tables.rows_to_dataframe/write_all_tables: a fully clean
run (zero findings) must still produce a full_results.csv that pandas can
read back -- caught building a notebook that unconditionally calls
pd.read_csv(full_results.csv) after every run, including a clean one.
Before the fix, rows_to_dataframe([]) built a plain pd.DataFrame([]) with
no columns at all, so the written CSV was completely empty (no header
row), and pd.read_csv raised EmptyDataError on it.
"""
import pandas as pd

from ontology_suite.checks.registry import Check, Registry
from ontology_suite.report.tables import rows_to_dataframe, write_all_tables


def _registry() -> Registry:
    check = Check(
        id="STR-001", category="structural", metric="m", default_severity="Violation",
        title="t", description="d", remediation="r", cucumber_feature="f", cucumber_scenario="s",
    )
    return Registry(checks={"STR-001": check}, namespace="https://example.org/oq/")


def test_rows_to_dataframe_empty_has_the_full_column_set():
    df = rows_to_dataframe([])
    assert df.empty
    assert list(df.columns) == [
        "check_id", "category", "title", "severity", "focus_node", "path", "value",
        "message", "remediation", "sources",
    ]


def test_write_all_tables_with_no_findings_produces_a_readable_full_results_csv(tmp_path):
    write_all_tables([], _registry(), tmp_path)
    csv_path = tmp_path / "full_results.csv"
    assert csv_path.is_file()
    df = pd.read_csv(csv_path)  # must not raise EmptyDataError
    assert df.empty
    assert "check_id" in df.columns
