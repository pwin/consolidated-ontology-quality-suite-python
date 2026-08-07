"""Tests for ontology_suite.docgen (ported from the standalone docgen3
project): extracts a JSON data model from an ontology and injects it into
the HTML documentation template.
"""
import json

from ontology_suite import config
from ontology_suite.docgen import build_documentation, extract_ontology_data

EXAMPLE_ONTOLOGY = "examples/ontology/domain.ttl"
GIST_ONTOLOGY = "examples/gist_versions_reference/gistCore14.1.0.ttl"


def test_extract_and_build_end_to_end(tmp_path):
    data_path = tmp_path / "data.json"
    html_path = tmp_path / "doc.html"

    extract_ontology_data.main(["--ontology", EXAMPLE_ONTOLOGY, "--prefix", "ex", "--out", str(data_path)])
    assert data_path.exists()

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(data["classes"]) == 12
    assert len(data["objectProperties"]) == 2
    assert len(data["datatypeProperties"]) == 4
    assert data["localPrefix"] == "ex"

    build_documentation.main(
        ["--data", str(data_path), "--template", str(config.DEFAULT_DOCGEN_TEMPLATE), "--out", str(html_path)]
    )
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "__ONTOLOGY_DATA_JSON__" not in html  # placeholder must be replaced
    assert '"localPrefix": "ex"' in html  # the extracted data actually got embedded


def test_extraction_matches_docgen3s_own_gist_benchmark(tmp_path):
    """docgen3's own README documents gistCore14.1.0 extracting to exactly
    96 classes / 66 object properties / 50 datatype properties; this is a
    real-world regression check that the port didn't change that."""
    data_path = tmp_path / "gist_data.json"
    extract_ontology_data.main(["--ontology", GIST_ONTOLOGY, "--prefix", "gist", "--out", str(data_path)])

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(data["classes"]) == 96
    assert len(data["objectProperties"]) == 66
    assert len(data["datatypeProperties"]) == 50
