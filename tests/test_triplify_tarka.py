"""`triplify` on tarka: every option the suite exposes reaches it, failures
are errors rather than empty files, and -- where an oxi-gen build is on this
machine -- tarka still gives oxi-gen's graph.

tarka replaced oxi-gen in 0.29.0 (see `ontology_suite/triplify/tarka.py`).
The oxi-gen comparison at the bottom is the same kind of cross-check as
pyshacl and pyoxigraph are for the other two engines: a second, independent
implementation kept to disagree with ours when ours is wrong. oxi-gen is not
on PyPI, so it is found the way the extension's parity tests find the
extension -- ``ONTOLOGY_SUITE_OXI_GEN_BIN``, or a build in a sibling
``../oxi-gen`` checkout -- and the comparison skips without one.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import rdflib
from rdflib.compare import to_isomorphic

from ontology_suite import cli, config, pipeline
from ontology_suite.triplify import tarka
from ontology_suite.triplify.discovery import TriplifyJob

EX = "http://ex.org/"
PEOPLE = (
    "id,name,age\n"
    "1,Ann,30\n"
    "2,Bob,41\n"
    "3,Cy,\n"
    "4,Di,29\n"
    "5,Ed,50\n"
)
QUERY = (
    f"PREFIX ex: <{EX}>\n"
    "CONSTRUCT { ?p a ex:Person ; ex:name ?name ; ex:age ?age }\n"
    f'WHERE {{ BIND(IRI(CONCAT("{EX}p/", ?id)) AS ?p) }}\n'
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _graph(path: Path) -> rdflib.Graph:
    return rdflib.Graph().parse(path, format="nt" if path.suffix == ".nt" else "turtle")


def _people(tmp_path: Path, csv_text: str = PEOPLE, query: str = QUERY, name: str = "people.csv") -> TriplifyJob:
    return TriplifyJob(csv_path=_write(tmp_path / name, csv_text), query_path=_write(tmp_path / "q.rq", query))


def _subjects(graph: rdflib.Graph) -> set[str]:
    return {str(s) for s in graph.subjects(rdflib.RDF.type, rdflib.URIRef(EX + "Person"))}


# --------------------------------------------------------------------------
# Each option reaches tarka
# --------------------------------------------------------------------------
def test_plain_run_writes_every_row(tmp_path):
    job = _people(tmp_path)
    result = tarka.run_tarka(job, tmp_path / "out.ttl")
    graph = _graph(result.output_path)
    assert _subjects(graph) == {f"{EX}p/{i}" for i in range(1, 6)}
    # Cy's empty age cell is unbound, as TARQL defines an empty cell, so
    # there is no ex:age triple for her: 5 types + 5 names + 4 ages.
    assert len(graph) == 14


def test_ntriples(tmp_path):
    result = tarka.run_tarka(_people(tmp_path), tmp_path / "out.nt", ntriples=True)
    text = result.output_path.read_text(encoding="utf-8")
    assert "@prefix" not in text and len(_graph(result.output_path)) == 14


def test_tab(tmp_path):
    job = _people(tmp_path, PEOPLE.replace(",", "\t"), name="people.tsv")
    assert len(_graph(tarka.run_tarka(job, tmp_path / "out.ttl", tab=True).output_path)) == 14


def test_delimiter(tmp_path):
    job = _people(tmp_path, PEOPLE.replace(",", ";"))
    assert len(_graph(tarka.run_tarka(job, tmp_path / "out.ttl", delimiter=";").output_path)) == 14


def test_no_header_row_names_columns_a_b_c(tmp_path):
    query = f'PREFIX ex: <{EX}>\nCONSTRUCT {{ ?p a ex:Person ; ex:name ?b }} WHERE {{ BIND(IRI(CONCAT("{EX}p/", ?a)) AS ?p) }}\n'
    job = _people(tmp_path, "1,Ann,30\n2,Bob,41\n", query)
    graph = _graph(tarka.run_tarka(job, tmp_path / "out.ttl", no_header_row=True).output_path)
    assert _subjects(graph) == {f"{EX}p/1", f"{EX}p/2"}


def test_normalize_upper_cases_the_column_names(tmp_path):
    query = QUERY.replace("?id", "?ID").replace("?name", "?NAME").replace("?age", "?AGE")
    job = _people(tmp_path, query=query)
    assert len(_graph(tarka.run_tarka(job, tmp_path / "out.ttl", normalize=True).output_path)) == 14
    # And without it the upper-case variables are simply unbound.
    assert len(_graph(tarka.run_tarka(job, tmp_path / "plain.ttl").output_path)) == 0


def test_test_rows_maps_only_the_first_n(tmp_path):
    graph = _graph(tarka.run_tarka(_people(tmp_path), tmp_path / "out.ttl", test_rows=2).output_path)
    assert _subjects(graph) == {f"{EX}p/1", f"{EX}p/2"}


def test_dedup(tmp_path):
    # Every row makes the same triple; dedup across rows leaves one.
    query = f"PREFIX ex: <{EX}>\nCONSTRUCT {{ ex:dataset ex:hasRows true }} WHERE {{ }}\n"
    job = _people(tmp_path, query=query)
    assert len(_graph(tarka.run_tarka(job, tmp_path / "out.nt", ntriples=True, dedup=1000).output_path)) == 1


def test_bound_is_true_for_a_cell_with_a_value(tmp_path):
    """TARQL's meaning, and a change from oxi-gen, which panicked on this
    query and left an empty file (see triplify/tarka.py)."""
    query = f'PREFIX ex: <{EX}>\nCONSTRUCT {{ ?p ex:age ?age }} WHERE {{ BIND(IRI(CONCAT("{EX}p/", ?id)) AS ?p) FILTER(BOUND(?age)) }}\n'
    graph = _graph(tarka.run_tarka(_people(tmp_path, query=query), tmp_path / "out.ttl").output_path)
    assert len(graph) == 4  # every row but Cy's


# --------------------------------------------------------------------------
# Failures are errors, not empty files
# --------------------------------------------------------------------------
def test_a_bad_query_is_an_error_with_tarkas_message(tmp_path):
    job = _people(tmp_path, query=f"PREFIX ex: <{EX}>\nCONSTRUCT {{ ?p ex:x ?y }} WHERE {{ BIND(\n")
    out = tmp_path / "out.ttl"
    with pytest.raises(tarka.TriplifyError) as info:
        tarka.run_tarka(job, out)
    assert info.value.returncode != 0
    assert "q.rq" in info.value.stderr
    assert not out.exists()


def test_a_missing_csv_is_an_error_and_leaves_no_file(tmp_path):
    """oxi-gen panicked here and left an empty output file, which a caller
    could not tell apart from a CSV that produced no triples."""
    job = TriplifyJob(csv_path=tmp_path / "nope.csv", query_path=_write(tmp_path / "q.rq", QUERY))
    out = tmp_path / "out.ttl"
    with pytest.raises(tarka.TriplifyError) as info:
        tarka.run_tarka(job, out)
    assert "nope.csv" in str(info.value)
    assert not out.exists()


def test_a_batch_keeps_going_past_a_failure(tmp_path):
    good = _people(tmp_path)
    bad = TriplifyJob(csv_path=tmp_path / "missing.csv", query_path=good.query_path)
    results, errors = tarka.run_tarka_batch([bad, good], tmp_path / "out")
    assert [r.output_path.name for r in results] == ["people.ttl"]
    assert [e.job.csv_path.name for e in errors] == ["missing.csv"]


# --------------------------------------------------------------------------
# The stage and the command
# --------------------------------------------------------------------------
ACME = config.REPO_ROOT / "examples" / "acme_robotics"


def test_the_stage_triplifies_acme_without_any_binary(tmp_path, monkeypatch):
    # Nothing to find: neither the variable nor a flag matters any more.
    monkeypatch.delenv("ONTOLOGY_SUITE_OXI_GEN_BIN", raising=False)
    stage = pipeline.run_triplify_stage(ACME, ACME, tmp_path)
    assert stage.warnings == []
    assert [p.name for p in stage.artifacts["output_paths"]] == ["employees.ttl"]
    assert len(_graph(stage.artifacts["output_paths"][0])) == 40
    assert stage.artifacts["engine"].startswith("tarka (semops-system ")


def test_oxi_gen_bin_is_ignored_with_a_warning(tmp_path):
    stage = pipeline.run_triplify_stage(ACME, ACME, tmp_path, oxi_gen_bin="C:/nowhere/oxi_gen.exe")
    assert len(stage.artifacts["output_paths"]) == 1
    assert any("--oxi-gen-bin" in w and "ignored" in w for w in stage.warnings)


def test_the_triplify_command(tmp_path, capsys):
    code = cli.main(["triplify", "--csv-dir", str(ACME), "--queries", str(ACME), "--out-dir", str(tmp_path),
                     "--oxi-gen-bin", "oxi_gen.exe"])
    out, err = capsys.readouterr()
    assert code == 0
    assert "Triplified 1 file(s)" in out
    assert "--oxi-gen-bin (oxi_gen.exe) is ignored" in err


# --------------------------------------------------------------------------
# oxi-gen, where there is one, as a second opinion
# --------------------------------------------------------------------------
def _oxi_gen() -> Path | None:
    candidates = []
    if os.environ.get("ONTOLOGY_SUITE_OXI_GEN_BIN"):
        candidates.append(Path(os.environ["ONTOLOGY_SUITE_OXI_GEN_BIN"]))
    release = config.REPO_ROOT.parent / "oxi-gen" / "target" / "release"
    candidates += [release / "oxi_gen.exe", release / "oxi_gen"]
    return next((c for c in candidates if c.is_file()), None)


OXI_GEN = _oxi_gen()
needs_oxi_gen = pytest.mark.skipif(
    OXI_GEN is None,
    reason="no oxi-gen build to compare with (set ONTOLOGY_SUITE_OXI_GEN_BIN, or build ../oxi-gen)",
)

EXAMPLES = config.REPO_ROOT / "examples"
PAIRS = {
    "animals": (EXAMPLES / "csv" / "animals.csv", EXAMPLES / "queries" / "animals.rq"),
    "acme-employees": (ACME / "employees.csv", ACME / "employees.rq"),
    "vehicles-transform": (EXAMPLES / "pattern_consistency" / "vehicles.csv", EXAMPLES / "pattern_consistency" / "transform.rq"),
    "vehicles-transform-fixed": (EXAMPLES / "pattern_consistency" / "vehicles.csv", EXAMPLES / "pattern_consistency" / "transform-fixed.rq"),
}


def _via_oxi_gen(job: TriplifyJob, out: Path, *flags: str) -> rdflib.Graph:
    subprocess.run([str(OXI_GEN), "-q", str(job.query_path), "-i", str(job.csv_path), "-o", str(out), *flags],
                   check=True, capture_output=True)
    return _graph(out)


@needs_oxi_gen
@pytest.mark.parametrize("name", sorted(PAIRS))
def test_tarka_gives_oxi_gens_graph_on_the_shipped_examples(name, tmp_path):
    csv_path, query_path = PAIRS[name]
    job = TriplifyJob(csv_path=csv_path, query_path=query_path)
    theirs = _via_oxi_gen(job, tmp_path / "oxi.ttl")
    ours = _graph(tarka.run_tarka(job, tmp_path / "tarka.ttl").output_path)
    assert len(ours) > 0
    assert to_isomorphic(ours) == to_isomorphic(theirs)


@needs_oxi_gen
@pytest.mark.parametrize("kwargs,flags", [
    ({"normalize": True}, ["--normalize"]),
    ({"test_rows": 2}, ["--test=2"]),
    ({"dedup": 1000}, ["--dedup=1000"]),
])
def test_tarka_gives_oxi_gens_graph_for_each_option(kwargs, flags, tmp_path):
    query = QUERY
    if kwargs.get("normalize"):
        query = QUERY.replace("?id", "?ID").replace("?name", "?NAME").replace("?age", "?AGE")
    job = _people(tmp_path, query=query)
    theirs = _via_oxi_gen(job, tmp_path / "oxi.ttl", *flags)
    ours = _graph(tarka.run_tarka(job, tmp_path / "tarka.ttl", **kwargs).output_path)
    assert len(ours) > 0
    assert to_isomorphic(ours) == to_isomorphic(theirs)
