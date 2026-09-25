"""`--reports minimal` writes the two files someone reads, and stops.

The default writes six artefacts because six audiences were imagined: a
person, a script, CI, a browser, and the plots the browser embeds. Plenty of
runs have none of those audiences -- a CI gate reads the exit code, a fixture
that seeds one defect does not need three charts of three findings -- and
paid for all six anyway, including the matplotlib rendering.

What this pins is the contract of the word "minimal": exactly findings.txt
and full_results.csv, the one for a person and the one for a script. Adding a
seventh report later is fine; adding it to *minimal* is the thing that needs
a reason, and this test is where that argument has to be had.
"""
import json

import pytest

from ontology_suite import cli
from ontology_suite.checks.merge import ResultRow
from ontology_suite.checks.registry import Registry
from ontology_suite import config

MINIMAL = {"findings.txt", "full_results.csv"}
EXTRAS = {"report.html", "cucumber.json"}


@pytest.fixture
def rows():
    return [
        ResultRow(check_id="STY-001", category="style", title=None, severity="Warning",
                  focus_node="https://example.org/m#person_record", path=None, value=None,
                  message="Class name not UpperCamelCase", remediation=None, sources=["sparql"]),
        ResultRow(check_id="QUA-005", category="quality", title=None, severity="Warning",
                  focus_node="https://example.org/m", path=None, value=None,
                  message="Ontology has no identifying IRI at all", remediation=None,
                  sources=["sparql"]),
    ]


@pytest.fixture
def registry():
    return Registry.load(config.DEFAULT_REGISTRY_PATH)


def written(directory):
    return {p.name for p in directory.iterdir()}


def test_minimal_writes_only_the_two_read_by_someone(tmp_path, rows, registry):
    cli._write_reports(rows, registry, tmp_path, "Test Report", {}, reports="minimal")
    assert written(tmp_path) == MINIMAL


def test_all_is_the_default_and_unchanged(tmp_path, rows, registry):
    cli._write_reports(rows, registry, tmp_path, "Test Report", {})
    produced = written(tmp_path)
    assert MINIMAL | EXTRAS <= produced
    assert {"plots", "features"} <= produced


def test_minimal_loses_no_finding(tmp_path, rows, registry):
    """Fewer files, not less said. The two it writes still carry every
    finding, which is what makes the flag safe to default to in CI."""
    minimal, everything = tmp_path / "minimal", tmp_path / "all"
    cli._write_reports(rows, registry, minimal, "Test Report", {}, reports="minimal")
    cli._write_reports(rows, registry, everything, "Test Report", {})

    assert (minimal / "full_results.csv").read_text(encoding="utf-8") == \
           (everything / "full_results.csv").read_text(encoding="utf-8")
    assert (minimal / "findings.txt").read_text(encoding="utf-8") == \
           (everything / "findings.txt").read_text(encoding="utf-8")

    for check in ("STY-001", "QUA-005"):
        assert check in (minimal / "findings.txt").read_text(encoding="utf-8")


def test_every_reporting_subcommand_accepts_it():
    """The flag is only useful if it is on every subcommand that writes
    reports. Asserted against the parser rather than a list kept here, so a
    new subcommand that writes reports and forgets the flag fails."""
    import argparse

    parser = cli.build_arg_parser()
    subcommands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name in ("ontology", "checks", "sketch", "data", "run"):
        options = {o for action in subcommands.choices[name]._actions
                   for o in action.option_strings}
        assert "--reports" in options, f"{name} writes reports but has no --reports"


def test_the_summary_does_not_advertise_a_file_it_did_not_write(tmp_path, rows, registry, capsys):
    """Printing a path to a report.html that is not there sends the reader
    looking for a file the run deliberately skipped."""
    cli._write_reports(rows, registry, tmp_path, "Test Report", {}, reports="minimal")
    cli._print_summary(rows, tmp_path, [])
    printed = capsys.readouterr().out
    assert "findings.txt" in printed
    assert "report.html" not in printed
