"""Tests for ontology_suite.pattern_consistency: the taxonomy<->transform
check that's genuinely new in that module, the SKOS-annotation-predicate
fix to dataquality.data_quality it depends on, and the real worked example
under examples/pattern_consistency/ (docs/MODELLING_PATTERN_CONSISTENCY.md).
"""
from rdflib import RDFS, Graph, Literal, Namespace, URIRef

from ontology_suite import pattern_consistency as pc
from ontology_suite.dataquality import data_quality

EXAMPLE_DIR = "examples/pattern_consistency"
ONTOLOGY = f"{EXAMPLE_DIR}/ontology.ttl"
TAXONOMY = f"{EXAMPLE_DIR}/taxonomy.ttl"
BROKEN_TRANSFORM = f"{EXAMPLE_DIR}/transform.rq"
FIXED_TRANSFORM = f"{EXAMPLE_DIR}/transform-fixed.rq"

EX = Namespace("https://example.org/vehicle-demo/")
GIST = Namespace("https://w3id.org/semanticarts/ns/ontology/gist/")


# --- data_quality.py's SKOS-annotation-predicate fix ------------------------

def test_skos_preflabel_is_not_flagged_as_an_undeclared_property():
    """Regression test for the ANNOTATION_PREDICATES fix: skos:prefLabel is
    SKOS's own lexical-label predicate (same role as rdfs:label), never
    meant to be locally re-declared -- before the fix, any SKOS-labeled
    taxonomy checked with check_conformance() flooded a false CNF-002 for
    every single skos:prefLabel triple."""
    ontology_graph = Graph()
    ontology_graph.parse(ONTOLOGY, format="turtle")
    declarations = data_quality.ontology_declarations(ontology_graph)

    data_graph = Graph()
    data_graph.add((EX.Petrol, RDFS.label, Literal("unused, just to have a triple")))
    from rdflib.namespace import SKOS
    data_graph.add((EX.Petrol, SKOS.prefLabel, Literal("Petrol", lang="en")))

    conformance = data_quality.check_conformance(declarations, data_graph)
    assert URIRef("http://www.w3.org/2004/02/skos/core#prefLabel") not in conformance["undeclared_properties_used"]


# --- check_taxonomy_references (synthetic, isolated) ------------------------

def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_taxonomy_reference_gap_detected(tmp_path):
    onto = _write(
        tmp_path / "onto.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Widget a owl:Class .\nex:hasColor a owl:ObjectProperty .\n",
    )
    taxonomy = _write(
        tmp_path / "taxonomy.ttl",
        "@prefix ex: <https://example.org/demo/> .\nex:Red a ex:Color .\nex:Blue a ex:Color .\n",
    )
    query = _write(
        tmp_path / "q.rq",
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT { ?item a ex:Widget ; ex:hasColor ex:Green . } WHERE { BIND(?x AS ?item) }\n",
    )
    findings = pc.check_taxonomy_references([query], [onto], [taxonomy])
    assert len(findings) == 1
    assert findings[0].term == "https://example.org/demo/Green"
    assert findings[0].property == "https://example.org/demo/hasColor"


def test_taxonomy_reference_that_exists_is_not_flagged(tmp_path):
    onto = _write(
        tmp_path / "onto.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Widget a owl:Class .\nex:hasColor a owl:ObjectProperty .\n",
    )
    taxonomy = _write(
        tmp_path / "taxonomy.ttl",
        "@prefix ex: <https://example.org/demo/> .\nex:Red a ex:Color .\n",
    )
    query = _write(
        tmp_path / "q.rq",
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT { ?item a ex:Widget ; ex:hasColor ex:Red . } WHERE { BIND(?x AS ?item) }\n",
    )
    findings = pc.check_taxonomy_references([query], [onto], [taxonomy])
    assert findings == []


def test_per_row_constructed_entities_are_not_treated_as_taxonomy_references(tmp_path):
    """A CSV-bound ?variable turned into a scratch `:entity` (the normal
    "this triple's object is data, not a controlled-vocabulary reference"
    case) must never be flagged -- only IRIs the query author wrote
    literally into the CONSTRUCT template."""
    onto = _write(
        tmp_path / "onto.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Widget a owl:Class .\nex:relatesTo a owl:ObjectProperty .\n",
    )
    taxonomy = _write(tmp_path / "taxonomy.ttl", "@prefix ex: <https://example.org/demo/> .\n")
    query = _write(
        tmp_path / "q.rq",
        "PREFIX ex: <https://example.org/demo/>\n"
        "CONSTRUCT { ?item a ex:Widget ; ex:relatesTo ?other . } "
        "WHERE { BIND(?x AS ?item) BIND(?y AS ?other) }\n",
    )
    findings = pc.check_taxonomy_references([query], [onto], [taxonomy])
    assert findings == []


# --- check_taxonomy_membership: identity-based, real-data taxonomy check ----
# (the taxonomy<->output-data boundary check_taxonomy_references structurally
# can't do, since a per-row dynamically-constructed value has no fixed
# literal in the query text -- found as a real, reproducible gap building an
# external worked example against this suite.)

def test_taxonomy_membership_catches_a_dynamically_built_reference_that_doesnt_exist(tmp_path):
    """check_taxonomy_references (query-text-only) can't see this at all --
    the department IRI is built per-row from a CSV column
    (BIND(IRI(CONCAT(...)) AS ?dept)), not hard-coded in the query -- so
    only real triplified output can expose the gap."""
    onto = _write(
        tmp_path / "onto.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "ex:Employee a owl:Class .\nex:Department a owl:Class .\n"
        "ex:worksIn a owl:ObjectProperty ; rdfs:domain ex:Employee ; rdfs:range ex:Department .\n",
    )
    taxonomy = _write(
        tmp_path / "taxonomy.ttl",
        "@prefix ex: <https://example.org/demo/> .\nex:ENG a ex:Department .\nex:QA a ex:Department .\n",
    )
    output = _write(
        tmp_path / "output.ttl",
        "@prefix ex: <https://example.org/demo/> .\n"
        "ex:e1 a ex:Employee ; ex:worksIn ex:ENG .\n"
        "ex:e2 a ex:Employee ; ex:worksIn ex:MKT .\n",  # MKT never declared in taxonomy.ttl
    )
    findings = pc.check_taxonomy_membership([output], [onto], [taxonomy])
    assert len(findings) == 1
    assert findings[0].term == "https://example.org/demo/MKT"
    assert findings[0].property == "https://example.org/demo/worksIn"


def test_taxonomy_membership_no_false_positive_for_valid_references(tmp_path):
    onto = _write(
        tmp_path / "onto.ttl",
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "ex:Employee a owl:Class .\nex:Department a owl:Class .\n"
        "ex:worksIn a owl:ObjectProperty ; rdfs:domain ex:Employee ; rdfs:range ex:Department .\n",
    )
    taxonomy = _write(
        tmp_path / "taxonomy.ttl",
        "@prefix ex: <https://example.org/demo/> .\nex:ENG a ex:Department .\n",
    )
    output = _write(
        tmp_path / "output.ttl",
        "@prefix ex: <https://example.org/demo/> .\nex:e1 a ex:Employee ; ex:worksIn ex:ENG .\n",
    )
    assert pc.check_taxonomy_membership([output], [onto], [taxonomy]) == []


def test_taxonomy_property_inference_is_subclass_aware():
    """examples/pattern_consistency/'s own ontology is exactly this shape:
    gist:isCategorizedBy's range is the generic gist:Category, but
    taxonomy.ttl's individuals are typed ex:FuelType, a
    rdfs:subClassOf gist:Category -- an exact-class-match-only inference
    would miss this ordinary, non-contrived pattern entirely."""
    inferred = pc._infer_taxonomy_properties(
        Graph().parse(ONTOLOGY, format="turtle"),
        Graph().parse(TAXONOMY, format="turtle"),
    )
    assert inferred.get(GIST.isCategorizedBy) == EX.FuelType


def test_taxonomy_membership_via_output_data_flag_end_to_end():
    """The full pattern-consistency --output-data path, using this repo's
    own bundled fixture -- the same scenario
    test_broken_transform_output_data_layer_alone_does_not_catch_the_gap
    demonstrates check_data_conformance() alone can't catch, now caught by
    the identity-based check running alongside it."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    output_path = tmp / "output.ttl"
    output_path.write_text(
        "@prefix ex: <https://example.org/vehicle-demo/> .\n"
        "@prefix gist: <https://w3id.org/semanticarts/ns/ontology/gist/> .\n"
        "ex:vehicle-1 a ex:Vehicle ; gist:isCategorizedBy ex:Gasoline .\n",
        encoding="utf-8",
    )
    report = pc.check_four_layer_consistency(
        [BROKEN_TRANSFORM], [ONTOLOGY], [TAXONOMY], output_data_paths=[str(output_path)],
    )
    assert report.taxonomy_output_data is not None
    assert len(report.taxonomy_output_data) == 1
    assert report.taxonomy_output_data[0].term == "https://example.org/vehicle-demo/Gasoline"
    assert not report.is_clean
    assert "taxonomy <-> output data" in pc.format_four_layer_report(report)


# --- real worked example: examples/pattern_consistency/ ---------------------

def test_broken_transform_flags_exactly_the_taxonomy_gap():
    report = pc.check_four_layer_consistency([BROKEN_TRANSFORM], [ONTOLOGY], [TAXONOMY])
    assert not report.is_clean
    assert report.ontology_transform.is_clean
    assert report.ontology_taxonomy == []
    assert len(report.taxonomy_transform) == 1
    assert report.taxonomy_transform[0].term == "https://example.org/vehicle-demo/Gasoline"


def test_fixed_transform_is_fully_clean():
    report = pc.check_four_layer_consistency([FIXED_TRANSFORM], [ONTOLOGY], [TAXONOMY])
    assert report.is_clean
    assert pc.format_four_layer_report(report) == (
        "No modelling-pattern inconsistencies found across ontology, taxonomy, transformation, or output data."
    )


def test_broken_transform_output_data_layer_alone_does_not_catch_the_gap(tmp_path):
    """The documented, important limitation: check_data_conformance()
    (ontology+taxonomy <-> output data) can't tell "this IRI's type just
    isn't in this file" from "this IRI doesn't exist anywhere" -- a
    plausible-looking but nonexistent reference is merely "unverifiable",
    not reported. Real per-row triplified output containing the same
    ex:Gasoline gap should still come back clean on this layer alone,
    which is exactly why taxonomy<->transformation is checked as its own
    layer instead of relying on reviewing output data for everything."""
    output_path = tmp_path / "output.ttl"
    output_path.write_text(
        "@prefix ex: <https://example.org/vehicle-demo/> .\n"
        "@prefix gist: <https://w3id.org/semanticarts/ns/ontology/gist/> .\n"
        "ex:vehicle-1 a ex:Vehicle ; gist:isCategorizedBy ex:Gasoline .\n",
        encoding="utf-8",
    )
    rows = pc.check_data_conformance([ONTOLOGY], [str(output_path)], "output-data")
    assert rows == []


# --- CLI ---------------------------------------------------------------------

def test_cli_reports_the_gap_and_exits_zero_by_default(capsys):
    exit_code = pc.main([
        "--queries", BROKEN_TRANSFORM, "--ontology", ONTOLOGY, "--taxonomy", TAXONOMY,
    ])
    assert exit_code == 0
    assert "undeclared_taxonomy_reference" in capsys.readouterr().out


def test_cli_fail_on_mismatch_exits_nonzero():
    exit_code = pc.main([
        "--queries", BROKEN_TRANSFORM, "--ontology", ONTOLOGY, "--taxonomy", TAXONOMY,
        "--fail-on-mismatch",
    ])
    assert exit_code == 1


def test_cli_clean_on_fixed_transform():
    exit_code = pc.main([
        "--queries", FIXED_TRANSFORM, "--ontology", ONTOLOGY, "--taxonomy", TAXONOMY,
        "--fail-on-mismatch",
    ])
    assert exit_code == 0


# --- consistency_dot / dot_export --------------------------------------------

def test_broken_transform_dot_colors_the_gap_red_and_the_valid_edge_green():
    dot_text = pc.consistency_dot([BROKEN_TRANSFORM], [ONTOLOGY], [TAXONOMY])

    assert 'label="a", color="darkgreen"' in dot_text  # ?vehicle a ex:Vehicle: declared class, OK
    assert '"https://example.org/vehicle-demo/Vehicle" [label="ex:Vehicle", color="darkgreen"' in dot_text

    assert 'label="gist:isCategorizedBy", color="red"' in dot_text  # the gap itself
    assert '"https://example.org/vehicle-demo/Gasoline" [label="ex:Gasoline", color="red"' in dot_text


def test_fixed_transform_dot_has_no_red_at_all():
    dot_text = pc.consistency_dot([FIXED_TRANSFORM], [ONTOLOGY], [TAXONOMY])
    assert "red" not in dot_text
    assert '"https://example.org/vehicle-demo/Petrol" [label="ex:Petrol", color="darkgreen"' in dot_text


def test_literal_objects_are_not_forced_gray_get_the_blue_border_default(tmp_path):
    """Consistency status (red/green/gray) was never a meaningful thing to
    say about a literal *value* -- only classes/properties/taxonomy
    references are ever checked. Regression test: consistency_dot() used
    to explicitly classify every node including literals, which always
    landed on gray (dot_export.NEUTRAL_COLOR) and silently overrode
    dot_export's own blue-border-for-literals default."""
    from ontology_suite.sketch import dot_export as de

    onto = tmp_path / "onto.ttl"
    onto.write_text(
        "@prefix ex: <https://example.org/demo/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "ex:Widget a owl:Class .\nex:hasLabel a owl:DatatypeProperty .\n",
        encoding="utf-8",
    )
    taxonomy = tmp_path / "taxonomy.ttl"
    taxonomy.write_text("@prefix ex: <https://example.org/demo/> .\n", encoding="utf-8")
    query = tmp_path / "q.rq"
    query.write_text(
        "PREFIX ex: <https://example.org/demo/>\n"
        'CONSTRUCT { ?item a ex:Widget ; ex:hasLabel "Some Label" . } WHERE { BIND(?x AS ?item) }\n',
        encoding="utf-8",
    )
    dot_text = pc.consistency_dot([str(query)], [str(onto)], [str(taxonomy)])
    assert f'"Some Label" [label="Some Label", color="{de.LITERAL_BORDER_COLOR}", fontcolor="black", shape=ellipse];' in dot_text
    # the constructed entity itself keeps its ordinary gray consistency-status classification
    assert 'label="item", color="gray40"' in dot_text


def test_write_consistency_dot_writes_a_file(tmp_path):
    out_path = tmp_path / "consistency.dot"
    written = pc.write_consistency_dot([BROKEN_TRANSFORM], [ONTOLOGY], [TAXONOMY], out_path)
    assert written == out_path
    assert out_path.is_file()
    assert out_path.read_text(encoding="utf-8").startswith("digraph sketch {")


def test_dot_is_valid_graphviz_syntax_brace_balanced():
    """Cheap syntax sanity check without requiring the `dot` binary to be
    installed in every environment this test suite runs in."""
    dot_text = pc.consistency_dot([BROKEN_TRANSFORM], [ONTOLOGY], [TAXONOMY])
    assert dot_text.count("{") == dot_text.count("}") == 1
    assert dot_text.strip().startswith("digraph")
    assert dot_text.strip().endswith("}")


def test_cli_dot_flag_writes_a_file(tmp_path, capsys):
    out_path = tmp_path / "out.dot"
    exit_code = pc.main([
        "--queries", BROKEN_TRANSFORM, "--ontology", ONTOLOGY, "--taxonomy", TAXONOMY,
        "--dot", str(out_path),
    ])
    assert exit_code == 0
    assert out_path.is_file()
    assert f"Wrote {out_path}" in capsys.readouterr().out


def test_rdf_type_renders_as_a_not_an_opaque_qname():
    """rdf:type is never declared as a PREFIX in a CONSTRUCT template (it's
    always written as Turtle's "a" shorthand), so without the dedicated
    dot_export._label() case for it, rdflib auto-mints an opaque "ns1:type"
    qname -- confirm the friendlier "a" rendering is what's actually used."""
    dot_text = pc.consistency_dot([FIXED_TRANSFORM], [ONTOLOGY], [TAXONOMY])
    assert "ns1:type" not in dot_text
    assert 'label="a"' in dot_text
