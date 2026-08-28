"""An import that fails to resolve must not fail *quietly*.

Both regressions here come from the same real user report. An ontology
whose owl:imports could not be resolved produced a flood of false
CNF-001/CNF-002 findings -- every term the missing ontology declared came
back "undeclared" -- and nothing in the default output said why. Two
separate defects combined to make it that hard to diagnose:

1. `io_utils.parse_graph` trusted the file extension absolutely, so an
   `.owl` file containing Turtle (what Protege writes by default) was
   handed to the RDF/XML parser, threw, and was silently skipped by
   `resolve_imports`'s `except Exception: continue`.
2. The unresolved list reached the user only through `--verbose`; it never
   became a `StageResult.warning`, so a default run showed the false
   findings with no accompanying cause.
"""
import rdflib

from ontology_suite import io_utils, pipeline
from ontology_suite.ontologyeval import ontology_evaluation

VOCAB_TURTLE = """\
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix v:    <https://example.org/imported/> .
<https://example.org/imported/vocab> a owl:Ontology .
v:Widget a owl:Class .
v:widgetCode a owl:DatatypeProperty ; rdfs:domain v:Widget ; rdfs:range xsd:string .
"""

MAIN_TURTLE = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://example.org/main> a owl:Ontology ;
  owl:imports <https://example.org/imported/vocab> .
"""


def _write_main(tmp_path):
    main = tmp_path / "main.ttl"
    main.write_text(MAIN_TURTLE, encoding="utf-8")
    imports = tmp_path / "imports"
    imports.mkdir()
    return main, imports


def test_sniff_format_reads_content_not_extension():
    assert io_utils.sniff_format(VOCAB_TURTLE.encode()) == "turtle"
    assert io_utils.sniff_format(b'<?xml version="1.0"?><rdf:RDF/>') == "xml"
    assert io_utils.sniff_format(b'{"@context": {}}') == "json-ld"
    # N-Triples has no header to sniff -- inconclusive must stay inconclusive
    # so the extension keeps deciding.
    assert io_utils.sniff_format(b"<https://a> <https://b> <https://c> .\n") is None


def test_extension_only_overridden_across_format_families(tmp_path):
    """A `.n3` file opening with `@prefix` sniffs as turtle, but n3 is a
    superset of turtle -- the more specific extension must win. Only a
    cross-family disagreement (turtle content, `.owl` extension) overrides."""
    assert io_utils.resolve_format("v.n3", VOCAB_TURTLE.encode()) == "n3"
    assert io_utils.resolve_format("v.ttl", VOCAB_TURTLE.encode()) == "turtle"
    assert io_utils.resolve_format("v.owl", VOCAB_TURTLE.encode()) == "turtle"
    assert io_utils.resolve_format("v.ttl", b'<?xml version="1.0"?><rdf:RDF/>') == "xml"


def test_turtle_content_in_an_owl_file_still_resolves(tmp_path):
    """The reported case: byte-identical content resolved as `.ttl` and
    silently vanished as `.owl`."""
    main, imports = _write_main(tmp_path)
    (imports / "vocab.owl").write_text(VOCAB_TURTLE, encoding="utf-8")

    graph, report = ontology_evaluation.resolve_imports(str(main), str(imports))

    assert report["unresolved"] == []
    assert len(report["resolved"]) == 1
    assert (rdflib.URIRef("https://example.org/imported/widgetCode"), None, None) in graph


def test_unparsable_candidate_is_reported_not_swallowed(tmp_path):
    """A file that genuinely cannot be parsed must name itself and its error
    in the report -- the old bare `except Exception: continue` left no trace
    at all."""
    main, imports = _write_main(tmp_path)
    (imports / "broken.ttl").write_text("this is not RDF at all {{{", encoding="utf-8")

    _graph, report = ontology_evaluation.resolve_imports(str(main), str(imports))

    assert report["unresolved"] == ["https://example.org/imported/vocab"]
    assert [entry["source"] for entry in report["unparsable"]] == [str(imports / "broken.ttl")]
    assert report["unparsable"][0]["error"]


def test_two_files_claiming_one_ontology_iri_are_reported(tmp_path):
    """A stale copy alongside a current one used to win silently by sort
    order, taking its missing terms with it."""
    main, imports = _write_main(tmp_path)
    (imports / "a-old.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")
    (imports / "b-new.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")

    _graph, report = ontology_evaluation.resolve_imports(str(main), str(imports))

    assert report["unresolved"] == []
    assert len(report["ambiguous"]) == 1
    entry = report["ambiguous"][0]
    assert entry["iri"] == "https://example.org/imported/vocab"
    assert entry["chosen"] == str(imports / "a-old.ttl")
    assert entry["also"] == [str(imports / "b-new.ttl")]


def test_unresolved_import_reaches_stage_warnings_without_verbose(tmp_path):
    """The diagnosis gap: the sketch stage reported the resulting false
    CNF findings but never said an import was missing."""
    main, imports = _write_main(tmp_path)  # imports/ deliberately left empty
    queries = tmp_path / "queries"
    queries.mkdir()
    (queries / "w.rq").write_text(
        'PREFIX v: <https://example.org/imported/>\n'
        'CONSTRUCT { ?x a v:Widget ; v:widgetCode ?c . }\n'
        'WHERE { BIND(IRI("https://example.org/x") AS ?x) }\n',
        encoding="utf-8",
    )

    stage = pipeline.run_sketch_stage(
        str(queries), tmp_path / "out", ontology_path=str(main), import_dir=str(imports),
    )

    # Filtered to the conformance rows: run_sketch_stage also runs the TARQL
    # BIND review (sketch/bind_analysis.py), whose findings have nothing to do
    # with import resolution and would otherwise make this assertion depend on
    # an unrelated check.
    conformance = [r.check_id for r in stage.rows if r.check_id.startswith("CNF-")]
    assert conformance == ["CNF-001", "CNF-002"]
    assert len(stage.warnings) == 1
    warning = stage.warnings[0]
    assert "UNRESOLVED" in warning
    assert "https://example.org/imported/vocab" in warning
    # The warning has to connect the cause to the symptom, or it is just
    # more output to scroll past.
    assert "CNF-001/CNF-002" in warning


def test_resolved_imports_produce_no_warning_noise(tmp_path):
    """The counterpart: a clean resolution must stay silent."""
    main, imports = _write_main(tmp_path)
    (imports / "vocab.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")
    queries = tmp_path / "queries"
    queries.mkdir()
    (queries / "w.rq").write_text(
        'PREFIX v: <https://example.org/imported/>\n'
        'CONSTRUCT { ?x a v:Widget ; v:widgetCode ?c . }\n'
        'WHERE { BIND(IRI("https://example.org/x") AS ?x) }\n',
        encoding="utf-8",
    )

    stage = pipeline.run_sketch_stage(
        str(queries), tmp_path / "out", ontology_path=str(main), import_dir=str(imports),
    )

    assert [r for r in stage.rows if r.check_id.startswith("CNF-")] == []
    assert stage.warnings == []
