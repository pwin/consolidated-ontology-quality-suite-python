"""Covers checks.repair: the ported registry-check quick-fix engine
(resources/repairs/*.ru applied via rdflib SPARQL 1.1 Update against an
in-memory graph)."""
from pathlib import Path

from rdflib import Graph, Namespace, RDF

from ontology_suite import config
from ontology_suite.checks.merge import ResultRow
from ontology_suite.checks.project_standards import ProjectStandards
from ontology_suite.checks.repair import apply_repair_to_file, compute_repair, has_repair_template, humanize_local_name

EX = Namespace("https://example.org/")


def _row(check_id, focus_node=None, path=None, value=None) -> ResultRow:
    return ResultRow(
        check_id=check_id, category=None, title=None, severity="Violation",
        focus_node=focus_node or "", path=path, value=value, message="", remediation=None,
    )


def test_has_repair_template():
    assert has_repair_template(config.DEFAULT_REPAIRS_DIR, "STR-005")
    assert not has_repair_template(config.DEFAULT_REPAIRS_DIR, "NOT-A-REAL-CHECK")


def test_humanize_local_name():
    assert humanize_local_name("https://example.org/hasOwner") == "has Owner"
    assert humanize_local_name("https://example.org/my_prop-name") == "my prop name"


def test_str005_insert_declares_the_domain_value_as_a_class():
    g = Graph()
    g.bind("ex", EX)
    row = _row("STR-005", focus_node=str(EX.foo), value=str(EX.Bar))
    outcome = compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {"ex": str(EX)})
    assert outcome is not None
    assert outcome.kind == "insert"
    assert len(outcome.added_quads) == 1
    (s, p, o) = outcome.added_quads[0]
    assert s == EX.Bar
    assert p == RDF.type
    assert str(o) == "http://www.w3.org/2002/07/owl#Class"


def test_qua001_insert_uses_derived_label_and_language_tag():
    g = Graph()
    g.bind("ex", EX)
    row = _row("QUA-001", focus_node=str(EX.hasOwner))
    standards = ProjectStandards(default_language_tag="fr")
    outcome = compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {"ex": str(EX)}, standards)
    assert outcome is not None
    assert outcome.kind == "insert"
    (s, p, o) = outcome.added_quads[0]
    assert s == EX.hasOwner
    assert str(p) == "http://www.w3.org/2000/01/rdf-schema#label"
    assert str(o) == "has Owner"
    assert o.language == "fr"


def test_mdl002_replace_switches_equivalentclass_to_subclassof_per_policy():
    g = Graph()
    g.bind("ex", EX)
    from rdflib.namespace import OWL
    g.add((EX.A, OWL.equivalentClass, EX.B))
    row = _row("MDL-002", focus_node=str(EX.A), value=str(EX.B))
    standards = ProjectStandards(equivalent_class_policy="subClassOf")
    outcome = compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {"ex": str(EX)}, standards)
    assert outcome is not None
    assert outcome.kind == "replace"
    from rdflib.namespace import RDFS
    assert (EX.A, OWL.equivalentClass, EX.B) not in outcome.result_graph
    assert (EX.A, RDFS.subClassOf, EX.B) in outcome.result_graph


def test_no_template_returns_none():
    g = Graph()
    row = _row("NOT-A-REAL-CHECK", focus_node=str(EX.x))
    assert compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {}) is None

    row_no_check_id = _row(None, focus_node=str(EX.x))
    assert compute_repair(config.DEFAULT_REPAIRS_DIR, row_no_check_id, g, {}) is None


def test_apply_repair_to_file_insert_appends_turtle_block(tmp_path):
    target = tmp_path / "onto.ttl"
    target.write_text("@prefix ex: <https://example.org/> .\nex:Existing a ex:Thing .\n", encoding="utf-8")

    g = Graph()
    g.parse(str(target), format="turtle")
    row = _row("STR-005", focus_node=str(EX.foo), value=str(EX.Bar))
    outcome = compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {"ex": str(EX)})
    assert outcome is not None

    new_content = apply_repair_to_file(outcome, target, {"ex": str(EX)}, write=True)
    assert "ex:Bar" in new_content
    assert target.read_text(encoding="utf-8") == new_content
    # original content preserved verbatim (append-only)
    assert new_content.startswith("@prefix ex: <https://example.org/> .\nex:Existing a ex:Thing .\n")


def test_multi_valued_and_path_expression_rows_do_not_break_the_update():
    """`build_repair_update` binds ?focusNode/?path/?value in one VALUES
    clause whether or not the template uses them, so a term that isn't a
    single IRI makes the whole UPDATE fail to parse -- for every check, not
    just the one the odd term came from.

    Two `merge.py` outputs are legitimately not IRIs: a joined list, for a
    result carrying several `sh:value`s (`STR-007` names both the subject
    and the object of an example triple), and a rendered property path, for
    a SHACL path expression (`LOG-001`'s `(rdfs:subClassOf)+`). `STR-007`'s
    own template touches only `?focusNode`, so it must still apply cleanly
    with either sitting in `?value`."""
    g = Graph()
    g.bind("ex", EX)
    row = _row(
        "STR-007",
        focus_node=str(EX.undeclaredPredicate),
        path=f"({str(EX.subClassOf)})+",
        value=f"{EX.subject}, {EX.object}",
    )

    outcome = compute_repair(config.DEFAULT_REPAIRS_DIR, row, g, {"ex": str(EX)})

    assert outcome is not None
    assert (EX.undeclaredPredicate, RDF.type, RDF.Property) in outcome.result_graph
