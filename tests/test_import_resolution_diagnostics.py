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
    # Two warnings now: this fixture's imports/ is empty, which the run also
    # reports (see test_an_import_dir_holding_no_ontologies_says_so). The one
    # under test here is the unresolved list, picked by name rather than by
    # position so adding a diagnostic cannot silently retarget this test.
    warning = next(w for w in stage.warnings if "UNRESOLVED" in w)
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


# ---------------------------------------------------------------------------
# The directory that was never opened
# ---------------------------------------------------------------------------
# `glob` returns nothing for a path that does not exist rather than raising,
# so an `--import-dir` pointing at a missing directory produced exactly the
# output an ontology with genuinely missing imports produces: every import
# unresolved, and a message naming the imports. Reported twice from real use,
# both times a mistyped path sitting on a command line beside two correct
# ones, and both times it read as a defect in import resolution rather than a
# typo. The directory is the thing the user can check, so the warning has to
# name it -- and name it *before* the unresolved list, which it explains.
def test_a_missing_import_dir_is_named_as_the_cause(tmp_path):
    main, imports = _write_main(tmp_path)
    (imports / "vocab.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")
    typo = tmp_path / "import"  # the real one is `imports`

    _, report = ontology_evaluation.resolve_imports(str(main), import_dir=str(typo))
    assert report["search_dir_missing"] is True
    assert report["search_dir_explicit"] is True
    assert report["unresolved"] == ["https://example.org/imported/vocab"]

    warnings = pipeline.import_warnings(str(main), report)
    assert "does not exist" in warnings[0], "the missing directory must be reported first"
    assert str(typo) in warnings[0]
    assert "UNRESOLVED" in warnings[1], "the unresolved list still follows"


def test_an_import_dir_holding_no_ontologies_says_so(tmp_path):
    """Distinct from the above and worth its own message: the path is right,
    the files are not there. Silence here sends the reader back to check a
    path that was correct all along."""
    main, imports = _write_main(tmp_path)  # created, deliberately left empty

    _, report = ontology_evaluation.resolve_imports(str(main), import_dir=str(imports))
    assert report["search_dir_missing"] is False
    assert report["candidate_count"] == 0

    warnings = pipeline.import_warnings(str(main), report)
    assert "holds no ontology files" in warnings[0]


def test_a_working_import_dir_adds_no_warning(tmp_path):
    """Both new warnings are conditional on something being wrong. Neither
    may fire on the ordinary case."""
    main, imports = _write_main(tmp_path)
    (imports / "vocab.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")

    _, report = ontology_evaluation.resolve_imports(str(main), import_dir=str(imports))
    assert report["search_dir_missing"] is False
    assert report["candidate_count"] == 1
    assert pipeline.import_warnings(str(main), report) == []


def test_no_import_dir_at_all_is_not_reported_as_missing(tmp_path):
    """Without `--import-dir` the search falls back to the ontology's own
    directory. That is a default, not a user-supplied path, so it must never
    be reported as one the user got wrong."""
    main, _ = _write_main(tmp_path)
    (tmp_path / "vocab.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")

    _, report = ontology_evaluation.resolve_imports(str(main))
    assert report["search_dir_explicit"] is False
    assert pipeline.import_warnings(str(main), report) == []


def test_a_candidate_with_no_ontology_header_cannot_resolve_anything(tmp_path):
    """Resolution matches a candidate by the ontology IRI it declares, so a
    file carrying no `owl:Ontology` header can never satisfy an import
    however much of the vocabulary it holds.

    Found in real use on a large reference-data file: 25k triples of exactly
    what the import asked for, no header, and therefore invisible to the
    matcher. The message is honest -- the import really is unresolved -- but
    the remedy is a one-line header in the candidate, not a path change.
    """
    main, imports = _write_main(tmp_path)
    headerless = VOCAB_TURTLE.replace(
        "<https://example.org/imported/vocab> a owl:Ontology .\n", ""
    )
    (imports / "vocab.ttl").write_text(headerless, encoding="utf-8")

    _, report = ontology_evaluation.resolve_imports(str(main), import_dir=str(imports))
    assert report["candidate_count"] == 1, "the file is found; it just cannot be matched"
    assert report["search_dir_missing"] is False
    assert report["unresolved"] == ["https://example.org/imported/vocab"]

    # And with the header, the same file resolves.
    (imports / "vocab.ttl").write_text(VOCAB_TURTLE, encoding="utf-8")
    _, report = ontology_evaluation.resolve_imports(str(main), import_dir=str(imports))
    assert report["unresolved"] == []
