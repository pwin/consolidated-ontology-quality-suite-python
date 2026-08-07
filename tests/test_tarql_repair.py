"""Covers repair.tarql_repair: turning sketch.prefix_alignment findings into
concrete, appliable diffs. Mirrors the scenarios in
tests/test_tarql_ontology_version_alignment.py (the finding side) but
checks the *fix* side -- that applying the suggested diff actually resolves
the alignment.
"""
from pathlib import Path

import pytest

from ontology_suite.repair import tarql_repair
from ontology_suite.repair.types import apply_suggestion
from ontology_suite.sketch import prefix_alignment as pa
from ontology_suite.versioning.diff import diff_ontologies
from ontology_suite.versioning.rename_detection import detect_renames
import rdflib


@pytest.fixture
def rename_scenario(tmp_path):
    onto_v1 = tmp_path / "domain-v1.ttl"
    onto_v1.write_text(
        "@prefix ex: <https://example.org/demo/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        'ex:Widget a owl:Class .\n'
        "ex:price a owl:DatatypeProperty ; rdfs:domain ex:Widget .\n",
        encoding="utf-8",
    )
    onto_v2 = tmp_path / "domain-v2.ttl"
    onto_v2.write_text(
        "@prefix ex: <https://example.org/demo/> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Product a owl:Class .\n"
        "ex:cost a owl:DatatypeProperty .\n"
        "ex:Widget owl:equivalentClass ex:Product .\n"
        "ex:price owl:equivalentProperty ex:cost .\n",
        encoding="utf-8",
    )
    tarql_v1 = tmp_path / "transform.rq"
    tarql_v1.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT {\n  ?item a ex:Widget ;\n    ex:price ?p .\n} WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )
    return {"onto_v1": onto_v1, "onto_v2": onto_v2, "tarql_v1": tarql_v1}


def test_rename_fix_resolves_the_alignment(rename_scenario, tmp_path):
    f = rename_scenario
    old_g = rdflib.Graph().parse(str(f["onto_v1"]), format="turtle")
    new_g = rdflib.Graph().parse(str(f["onto_v2"]), format="turtle")
    diff, _bump = diff_ontologies(old_g, new_g)
    renames = detect_renames(diff, new_g)

    report = pa.check_tarql_ontology_alignment([f["tarql_v1"]], [f["onto_v2"]])
    assert not report.is_clean

    suggestions = tarql_repair.suggest_repairs(report, [f["onto_v2"]], [f["tarql_v1"]], renames=renames)
    rename_suggestions = [s for s in suggestions if s.kind == "rename_iri"]
    assert len(rename_suggestions) == 1
    assert rename_suggestions[0].confidence == 1.0

    apply_suggestion(rename_suggestions[0], write=True)
    fixed_report = pa.check_tarql_ontology_alignment([f["tarql_v1"]], [f["onto_v2"]])
    assert fixed_report.is_clean


def test_namespace_mismatch_fix_resolves_the_alignment(tmp_path):
    onto = tmp_path / "domain.ttl"
    onto.write_text(
        "@prefix ex: <https://example.org/v2/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Widget a owl:Class .\n",
        encoding="utf-8",
    )
    tarql = tmp_path / "transform.rq"
    tarql.write_text(
        "PREFIX ex: <https://example.org/v1/>\nCONSTRUCT { ?item a ex:Widget . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )

    report = pa.check_tarql_ontology_alignment([tarql], [onto])
    assert len(report.prefix_misalignments) == 1
    assert report.prefix_misalignments[0].kind == "namespace_mismatch"

    suggestions = tarql_repair.suggest_repairs(report, [onto], [tarql])
    # exactly one suggestion: the prefix fix subsumes the undeclared-term finding for the same namespace
    assert len(suggestions) == 1
    assert suggestions[0].kind == "update_prefix"

    apply_suggestion(suggestions[0], write=True)
    assert "https://example.org/v2/" in tarql.read_text(encoding="utf-8")
    fixed_report = pa.check_tarql_ontology_alignment([tarql], [onto])
    assert fixed_report.is_clean


def test_namespace_mismatch_with_multiple_candidates_is_lower_confidence(tmp_path):
    onto_a = tmp_path / "a.ttl"
    onto_a.write_text("@prefix ex: <https://example.org/a/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:X a owl:Class .\n", encoding="utf-8")
    onto_b = tmp_path / "b.ttl"
    onto_b.write_text("@prefix ex: <https://example.org/b/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Y a owl:Class .\n", encoding="utf-8")
    tarql = tmp_path / "transform.rq"
    tarql.write_text("PREFIX ex: <https://example.org/c/>\nCONSTRUCT { ?item a ex:X . } WHERE { BIND(?x AS ?item) }\n", encoding="utf-8")

    report = pa.check_tarql_ontology_alignment([tarql], [onto_a, onto_b])
    suggestions = tarql_repair.check_prefix_fixes(report.prefix_misalignments, [onto_a, onto_b])
    assert len(suggestions) == 1
    assert suggestions[0].confidence == 0.5


def test_prefix_name_mismatch_fix_renames_the_label(tmp_path):
    onto = tmp_path / "domain.ttl"
    onto.write_text(
        "@prefix demo: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\ndemo:Widget a owl:Class .\n",
        encoding="utf-8",
    )
    tarql = tmp_path / "transform.rq"
    tarql.write_text(
        "PREFIX ex: <https://example.org/demo/>\nCONSTRUCT { ?item a ex:Widget . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )

    report = pa.check_tarql_ontology_alignment([tarql], [onto])
    assert report.prefix_misalignments[0].kind == "prefix_name_mismatch"

    suggestions = tarql_repair.check_prefix_fixes(report.prefix_misalignments, [onto])
    assert len(suggestions) == 1
    assert suggestions[0].kind == "rename_prefix"
    apply_suggestion(suggestions[0], write=True)
    text = tarql.read_text(encoding="utf-8")
    assert "PREFIX demo: <https://example.org/demo/>" in text
    assert "demo:Widget" in text
    assert "ex:" not in text


def test_prefix_name_mismatch_skipped_when_target_label_already_used_for_something_else(tmp_path):
    """tarql binds both 'ex:' and 'demo:' to two different namespaces than
    the ontology expects. The ontology's own label for
    https://example.org/demo/ is 'demo:' -- so renaming tarql's 'ex:' to
    'demo:' would collide with tarql's own unrelated 'demo:' prefix, and
    must be skipped. tarql's 'demo:' mismatch is a separate, independent
    problem (a namespace_mismatch on 'demo:' itself) and *does* still get
    its own suggestion -- fixing one problem shouldn't silently swallow
    the other."""
    onto = tmp_path / "domain.ttl"
    onto.write_text(
        "@prefix demo: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\ndemo:Widget a owl:Class .\n",
        encoding="utf-8",
    )
    tarql = tmp_path / "transform.rq"
    tarql.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        "PREFIX demo: <https://example.org/unrelated/>\n"
        "CONSTRUCT { ?item a ex:Widget ; demo:tag ?t . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )
    report = pa.check_tarql_ontology_alignment([tarql], [onto])
    suggestions = tarql_repair.check_prefix_fixes(report.prefix_misalignments, [onto])
    # no suggestion renames the 'ex:' prefix to 'demo:' (would collide)
    assert not any(s.kind == "rename_prefix" and "'ex:'" in s.description for s in suggestions)
    # the independent 'demo:' namespace_mismatch still gets its own, unrelated fix
    assert any(s.kind == "update_prefix" and s.target_file == str(tarql) for s in suggestions)


def test_undeclared_namespace_gets_no_suggestion(tmp_path):
    onto = tmp_path / "domain.ttl"
    onto.write_text("@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://example.org/x> a owl:Class .\n", encoding="utf-8")
    tarql = tmp_path / "transform.rq"
    tarql.write_text(
        "PREFIX foreign: <https://totally-external.example.org/ns/>\n"
        "CONSTRUCT { ?item a foreign:Thing . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )
    report = pa.check_tarql_ontology_alignment([tarql], [onto])
    assert report.prefix_misalignments[0].kind == "undeclared_namespace"
    suggestions = tarql_repair.check_prefix_fixes(report.prefix_misalignments, [onto])
    assert suggestions == []


def test_ontology_stub_suggestion_for_genuinely_new_vocabulary(tmp_path):
    onto = tmp_path / "domain.ttl"
    onto.write_text(
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Existing a owl:Class .\n",
        encoding="utf-8",
    )
    tarql = tmp_path / "transform.rq"
    tarql.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT { ?item a ex:NewThing . } WHERE { BIND(?x AS ?item) }\n",
        encoding="utf-8",
    )
    report = pa.check_tarql_ontology_alignment([tarql], [onto])
    suggestions = tarql_repair.suggest_ontology_stubs(report.undeclared_terms, [], onto)
    assert len(suggestions) == 1
    apply_suggestion(suggestions[0], write=True)
    assert "ex:NewThing a owl:Class ." in onto.read_text(encoding="utf-8")
    fixed_report = pa.check_tarql_ontology_alignment([tarql], [onto])
    assert fixed_report.is_clean
