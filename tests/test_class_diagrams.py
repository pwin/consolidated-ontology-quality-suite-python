"""Tests for ontology_suite.docgen.class_diagrams: per-class .dot/.svg/.png/
.ttl diagram generation for docgen's HTML output, scoped to local classes
by default (see docs/ARCHITECTURE.md's docgen section and the module's own
docstring for why imported classes are opt-in, not automatic).
"""
import shutil

import pytest
import rdflib
from rdflib import Namespace, OWL, RDF, RDFS

from ontology_suite import pipeline
from ontology_suite.docgen import class_diagrams as cd

EX = Namespace("https://example.org/demo/")
GIST = Namespace("https://w3id.org/semanticarts/ns/ontology/gist/")

DOT_AVAILABLE = shutil.which("dot") is not None


def _ontology_graph():
    g = rdflib.Graph(bind_namespaces="none")
    g.bind("ex", EX)
    g.bind("gist", GIST)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.add((EX.Vehicle, RDF.type, OWL.Class))
    g.add((EX.Vehicle, RDFS.label, rdflib.Literal("Vehicle")))
    g.add((EX.Vehicle, RDFS.isDefinedBy, EX.DemoOntology))
    g.add((EX.Vehicle, RDFS.subClassOf, GIST.PhysicalIdentifiableItem))
    # a blank-node restriction, to exercise CBD's recursive bnode traversal
    restriction = rdflib.BNode()
    g.add((EX.Vehicle, RDFS.subClassOf, restriction))
    g.add((restriction, RDF.type, OWL.Restriction))
    g.add((restriction, OWL.onProperty, EX.hasEngine))
    g.add((restriction, OWL.someValuesFrom, EX.Engine))
    g.add((EX.Engine, RDF.type, OWL.Class))
    return g


def _doc_data():
    return {
        "namespaces": [
            {"prefix": "ex", "uri": str(EX)},
            {"prefix": "gist", "uri": str(GIST)},
        ],
        "classes": [{"id": "ex:Vehicle"}, {"id": "ex:Engine"}],
        "externalReuse": [
            {"id": "gist:PhysicalIdentifiableItem", "kind": "Class"},
            {"id": "gist:isCategorizedBy", "kind": "ObjectProperty"},
        ],
    }


# --- safe_filename -----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("ex:Vehicle", "Vehicle"),
    ("https://example.org/demo#Vehicle", "Vehicle"),
    ("https://example.org/demo/Vehicle", "Vehicle"),
    ("ex:Vehicle Type!!", "Vehicle_Type"),
])
def test_safe_filename(value, expected):
    assert cd.safe_filename(value) == expected


def test_safe_filename_never_empty():
    assert cd.safe_filename("ex:") == "class"


# --- concise_bounded_description --------------------------------------------

def test_cbd_includes_subject_triples_and_recurses_into_blank_nodes():
    g = _ontology_graph()
    cbd = cd.concise_bounded_description(g, EX.Vehicle)

    assert (EX.Vehicle, RDF.type, OWL.Class) in cbd
    assert (EX.Vehicle, RDFS.subClassOf, GIST.PhysicalIdentifiableItem) in cbd

    restriction = next(o for o in cbd.objects(EX.Vehicle, RDFS.subClassOf) if isinstance(o, rdflib.BNode))
    assert (restriction, OWL.onProperty, EX.hasEngine) in cbd
    assert (restriction, OWL.someValuesFrom, EX.Engine) in cbd


def test_cbd_does_not_expand_past_named_nodes():
    """gist:PhysicalIdentifiableItem is referenced (as an edge endpoint)
    but its own definition triples must not be pulled in -- a local
    class's diagram shouldn't silently balloon into gist's own graph."""
    g = _ontology_graph()
    g.add((GIST.PhysicalIdentifiableItem, RDFS.label, rdflib.Literal("should not appear")))
    cbd = cd.concise_bounded_description(g, EX.Vehicle)
    assert (GIST.PhysicalIdentifiableItem, RDFS.label, rdflib.Literal("should not appear")) not in cbd


def test_cbd_of_unrelated_class_is_independent():
    g = _ontology_graph()
    cbd = cd.concise_bounded_description(g, EX.Engine)
    assert list(cbd.triples((EX.Engine, None, None))) == [(EX.Engine, RDF.type, OWL.Class)]


# --- _trim_for_diagram: "a owl:Class" and rdfs:isDefinedBy suppressed ------
# from the diagram only (both are true of every class diagrammed this way,
# so showing them is pure repetition, not information -- see docs/CLASS_DIAGRAMS.md).

def test_trim_for_diagram_drops_only_the_self_type_and_isdefinedby_triples():
    g = _ontology_graph()
    trimmed = cd._trim_for_diagram(g, EX.Vehicle)
    assert (EX.Vehicle, RDF.type, OWL.Class) not in trimmed
    assert (EX.Vehicle, RDFS.isDefinedBy, EX.DemoOntology) not in trimmed
    assert (EX.Engine, RDF.type, OWL.Class) in trimmed  # a different subject's "a owl:Class" is untouched
    assert (EX.Vehicle, RDFS.subClassOf, GIST.PhysicalIdentifiableItem) in trimmed  # everything else survives


def test_generated_dot_omits_a_owl_class_and_isdefinedby_but_ttl_keeps_both(tmp_path):
    """The exact scenario the user reported: every class diagram repeated
    a redundant "{class} a owl:Class" edge and a "{class} rdfs:isDefinedBy
    {ontology}" edge -- pure clutter, since the fact it's a *class* diagram
    for *this* ontology's documentation already says both. Confirms both
    are gone from the rendered .dot but the .ttl export (a complete data
    artifact, not a picture) still has them."""
    g = _ontology_graph()
    result = cd.generate_class_diagram(g, EX.Vehicle, "ex:Vehicle", tmp_path, render_images=False)
    dot_text = result.dot_path.read_text(encoding="utf-8")
    assert "owl:Class" not in dot_text
    assert "isDefinedBy" not in dot_text
    ttl_text = result.ttl_path.read_text(encoding="utf-8")
    assert "a owl:Class" in ttl_text
    assert "isDefinedBy" in ttl_text


# --- generate_class_diagram (dot/ttl always; svg/png only if requested) -----

def test_generate_class_diagram_without_images(tmp_path):
    g = _ontology_graph()
    result = cd.generate_class_diagram(g, EX.Vehicle, "ex:Vehicle", tmp_path, render_images=False)
    assert result.dot_path.is_file()
    assert result.ttl_path.is_file()
    assert result.svg_path is None
    assert result.png_path is None
    assert "ex:Vehicle" in result.dot_path.read_text(encoding="utf-8")


@pytest.mark.skipif(not DOT_AVAILABLE, reason="Graphviz `dot` not on PATH")
def test_generate_class_diagram_with_images(tmp_path):
    g = _ontology_graph()
    result = cd.generate_class_diagram(g, EX.Vehicle, "ex:Vehicle", tmp_path, render_images=True)
    assert result.svg_path.is_file()
    assert result.png_path.is_file()
    assert result.svg_path.read_text(encoding="utf-8").startswith("<?xml")


# --- generate_class_diagrams (local-only by default) -------------------------

def test_generate_class_diagrams_is_local_only_by_default(tmp_path):
    g = _ontology_graph()
    generated = cd.generate_class_diagrams(g, _doc_data(), tmp_path, render_images=False)
    assert set(generated.keys()) == {"ex:Vehicle", "ex:Engine"}


def test_generate_class_diagrams_include_external(tmp_path):
    g = _ontology_graph()
    generated = cd.generate_class_diagrams(g, _doc_data(), tmp_path, include_external=True, render_images=False)
    assert set(generated.keys()) == {"ex:Vehicle", "ex:Engine", "gist:PhysicalIdentifiableItem"}
    # gist:isCategorizedBy is an ObjectProperty, not a Class -- never diagrammed
    assert "gist:isCategorizedBy" not in generated


# --- patch_doc_data_with_diagrams --------------------------------------------

def test_patch_doc_data_adds_relative_paths_only_where_generated(tmp_path):
    doc_data = _doc_data()
    g = _ontology_graph()
    generated = cd.generate_class_diagrams(g, doc_data, tmp_path / "class-diagrams", render_images=False)
    cd.patch_doc_data_with_diagrams(doc_data, generated, tmp_path)

    vehicle = next(c for c in doc_data["classes"] if c["id"] == "ex:Vehicle")
    assert vehicle["diagram"]["ttl"] == "class-diagrams/Vehicle.ttl"
    assert vehicle["diagram"]["svg"] is None  # render_images=False

    gist_entry = next(r for r in doc_data["externalReuse"] if r["id"] == "gist:PhysicalIdentifiableItem")
    assert "diagram" not in gist_entry  # include_external was never requested


# --- end-to-end via pipeline.run_docgen_stage --------------------------------

def test_run_docgen_stage_generates_local_class_diagrams(tmp_path):
    stage = pipeline.run_docgen_stage("examples/ontology/domain.ttl", tmp_path, prefix="ex")

    diagrams = stage.artifacts["class_diagrams"]
    assert len(diagrams) == 12  # examples/ontology/domain.ttl's own class count (test_docgen.py)
    assert all(d.ttl_path.is_file() for d in diagrams.values())
    assert all(d.dot_path.is_file() for d in diagrams.values())
    if DOT_AVAILABLE:
        assert all(d.svg_path and d.svg_path.is_file() for d in diagrams.values())
        assert stage.warnings == []
    else:
        assert all(d.svg_path is None for d in diagrams.values())
        assert any("dot" in w for w in stage.warnings)


def test_run_docgen_stage_class_diagrams_can_be_disabled(tmp_path):
    stage = pipeline.run_docgen_stage(
        "examples/ontology/domain.ttl", tmp_path, prefix="ex", class_diagrams=False,
    )
    assert stage.artifacts["class_diagrams"] == {}
    assert not (tmp_path / "class-diagrams").exists()


def test_html_output_embeds_diagram_paths_and_no_leftover_placeholder(tmp_path):
    stage = pipeline.run_docgen_stage("examples/ontology/domain.ttl", tmp_path, prefix="ex")
    html = stage.artifacts["html_path"].read_text(encoding="utf-8")
    assert "__ONTOLOGY_DATA_JSON__" not in html
    assert "class-diagrams/Mammal.ttl" in html
