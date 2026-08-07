"""Covers consistency.py: the top-level local-file API tying version-diff,
rename detection, and TARQL alignment/repair together."""
import rdflib

from ontology_suite import consistency
from ontology_suite.versioning.diff import BumpLevel


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_check_consistency_full_pipeline(tmp_path):
    onto_v1 = _write(
        tmp_path / "domain-v1.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Widget a owl:Class .\n",
    )
    onto_v2 = _write(
        tmp_path / "domain-v2.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Product a owl:Class .\nex:Widget owl:equivalentClass ex:Product .\n",
    )
    tarql = _write(
        tmp_path / "transform.rq",
        "PREFIX ex: <https://example.org/demo/>\nCONSTRUCT { ?item a ex:Widget . } WHERE { BIND(?x AS ?item) }\n",
    )

    report = consistency.check_consistency(onto_v2, old_ontology=onto_v1, tarql_sources=[tarql])

    assert report.bump == BumpLevel.MAJOR
    assert len(report.renames) == 1
    assert not report.is_clean
    assert len(report.repairs) == 1
    assert report.repairs[0].kind == "rename_iri"

    text = consistency.format_consistency_report(report)
    assert "Suggested version bump: MAJOR" in text
    assert "suggested repair(s)" in text


def test_check_consistency_without_old_ontology_skips_version_diff(tmp_path):
    onto = _write(tmp_path / "domain.ttl", "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://example.org/x> a owl:Class .\n")
    tarql = _write(tmp_path / "transform.rq", "CONSTRUCT { <https://example.org/y> a <https://example.org/x> . } WHERE {}\n")

    report = consistency.check_consistency(onto, tarql_sources=[tarql])
    assert report.ontology_diff is None
    assert report.bump is None
    assert report.renames == []
    assert report.alignment is not None


def test_check_consistency_without_tarql_sources_skips_alignment(tmp_path):
    onto_v1 = _write(tmp_path / "v1.ttl", "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://example.org/x> a owl:Class .\n")
    onto_v2 = _write(tmp_path / "v2.ttl", "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://example.org/x> a owl:Class .\n<https://example.org/y> a owl:Class .\n")

    report = consistency.check_consistency(onto_v2, old_ontology=onto_v1)
    assert report.bump == BumpLevel.MINOR
    assert report.alignment is None
    assert report.repairs == []
    assert report.is_clean  # no alignment run means nothing to flag as unclean


def test_write_repair_patches_and_apply_repairs(tmp_path):
    onto = _write(
        tmp_path / "domain.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Existing a owl:Class .\n",
    )
    tarql = _write(
        tmp_path / "transform.rq",
        "PREFIX ex: <https://example.org/demo/>\nCONSTRUCT { ?item a ex:NewThing . } WHERE { BIND(?x AS ?item) }\n",
    )
    report = consistency.check_consistency(onto, tarql_sources=[tarql])
    assert len(report.repairs) == 1

    out_dir = tmp_path / "patches"
    written = consistency.write_repair_patches(report.repairs, out_dir)
    assert len(written) == 1
    assert written[0].exists()
    assert "ex:NewThing" in written[0].read_text(encoding="utf-8")
    # dry-run: target file untouched
    assert "NewThing" not in onto.read_text(encoding="utf-8")

    applied = consistency.apply_repairs(report.repairs)
    assert len(applied) == 1
    assert "ex:NewThing a owl:Class ." in onto.read_text(encoding="utf-8")


def test_apply_repairs_respects_min_confidence(tmp_path):
    onto_a = _write(tmp_path / "a.ttl", "@prefix ex: <https://example.org/a/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:X a owl:Class .\n")
    onto_b = _write(tmp_path / "b.ttl", "@prefix ex: <https://example.org/b/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Y a owl:Class .\n")
    tarql = _write(tmp_path / "transform.rq", "PREFIX ex: <https://example.org/c/>\nCONSTRUCT { ?item a ex:X . } WHERE { BIND(?x AS ?item) }\n")

    report = consistency.check_consistency(onto_a, tarql_sources=[tarql], ontology_paths=[onto_a, onto_b])
    ambiguous = [r for r in report.repairs if r.confidence < 0.6]
    assert ambiguous  # the ambiguous namespace_mismatch fix (two candidates) should be present

    applied = consistency.apply_repairs(report.repairs, min_confidence=0.9)
    assert ambiguous[0] not in applied
