"""LOG-002 counts values, not spellings.

The check used to be one filter::

    FILTER(STR(?o1) < STR(?o2))

which did two jobs with one expression -- order the pair so each is reported
once, and decide whether the two values differ -- and got the second wrong,
because STR() returns a lexical form and two lexical forms are not two
values. Three faults came out of that one line, and they were measured on the
nine cases below before anything was changed:

* **owl:sameAs.** Two names for one individual were reported as two values.
  Live, and the defect that was reported from the field.
* **Value-equal literals.** ``"80"`` and ``"80.0"^^xsd:decimal`` are one
  value with two lexical forms. This does *not* reproduce on holosdb, which
  matches them as one value before the filter is ever reached -- measured:
  the store holds two quads and the self-join yields one pair. It is fixed
  anyway, because these `.rq` files are the *portable* formulation: the VS
  Code extension runs the same file, and so may anyone else's engine. A check
  that is correct only because one engine is forgiving is the thing
  `checks/holos_sparql.py` exists to stop.
* **Blank nodes, the false negative.** SPARQL 1.1 17.4.2.5 defines STR() for
  literals and IRIs only, so STR() of a blank node is a type error, an error
  in a FILTER drops the row, and a functional property with two distinct
  blank-node values was reported as *clean*. That is how STY-003 shipped
  broken, and it is the worse half: a false positive is an argument, a false
  negative is silence.

Of the nine cases, the old filter was wrong on four.

Both formulations are tested, because LOG-002 is dual-formulated and the two
arms must agree on focus node and path or one defect arrives twice under one
id. Neither file is edited alone.
"""
import pytest
import rdflib

from ontology_suite import config
from ontology_suite.checks.holos_sparql import HolosSession
from ontology_suite.checks.shacl_native_runner import available as engine_available

SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")

HEADER = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <https://example.org/trail/> .
ex:foundedBy a owl:FunctionalProperty .
"""

# (id, body, how many findings LOG-002 should report)
CASES = [
    ("sameAs_forward",
     "ex:abbey ex:foundedBy ex:aelfric , ex:aelfricOfEynsham . ex:aelfric owl:sameAs ex:aelfricOfEynsham .", 0),
    ("sameAs_reversed",
     "ex:abbey ex:foundedBy ex:aelfric , ex:aelfricOfEynsham . ex:aelfricOfEynsham owl:sameAs ex:aelfric .", 0),
    ("sameAs_two_hops",
     "ex:abbey ex:foundedBy ex:a , ex:c . ex:a owl:sameAs ex:b . ex:b owl:sameAs ex:c .", 0),
    ("one_value",
     "ex:abbey ex:foundedBy ex:aelfric .", 0),
    ("decimal_spellings",
     'ex:abbey ex:foundedBy "80"^^xsd:decimal , "80.0"^^xsd:decimal .', 0),
    ("value_equal_across_numeric_datatypes",
     'ex:abbey ex:foundedBy "80"^^xsd:integer , "80.0"^^xsd:decimal .', 0),
    ("two_different_iris",
     "ex:abbey ex:foundedBy ex:trustA , ex:trustB .", 1),
    ("two_different_strings",
     'ex:abbey ex:foundedBy "Aelfric" , "Dunstan" .', 1),
    ("two_blank_nodes",
     'ex:abbey ex:foundedBy [ ex:name "A" ] , [ ex:name "B" ] .', 1),
]

LOG_002_RQ = (config.DEFAULT_SPARQL_DIR / "logical" / "closure-safe" / "LOG-002.rq").read_text(encoding="utf-8")


def _graph(body):
    return rdflib.Graph().parse(data=HEADER + body, format="turtle")


def _findings(results):
    """One entry per (focus node, path).

    The join is symmetric so an offending pair matches in both orders; the
    two solutions are identical on focus node, path and value and collapse
    under the suite's dedup key. Counted the same way here.
    """
    return {
        (results.value(r, SH.focusNode), results.value(r, SH.resultPath))
        for r in set(results.subjects(rdflib.RDF.type, SH.ValidationResult))
    }


@pytest.mark.parametrize("case_id,body,expected", CASES, ids=[c[0] for c in CASES])
def test_sparql_arm(case_id, body, expected):
    with HolosSession(_graph(body)) as session:
        found = _findings(session.construct(LOG_002_RQ))
    assert len(found) == expected, f"{case_id}: {sorted(map(str, (f for pair in found for f in pair)))}"


@pytest.mark.parametrize("case_id,body,expected", CASES, ids=[c[0] for c in CASES])
def test_shacl_arm(case_id, body, expected):
    """The shape must reach the same verdict as the query, case for case."""
    assert engine_available(), "the `shacl` engine is a runtime dependency"
    from ontology_suite.checks.registry import Registry
    from ontology_suite.checks.shacl_native_runner import run_shacl_native_rows
    from ontology_suite.checks.shapes import load_shapes_graph

    # Resolved to check ids through the registry, the way the pipeline does
    # it, rather than filtered by path: the full shapes graph has plenty to
    # say about a four-triple fixture, and other shapes report on
    # `ex:foundedBy` too -- isolating by path counted those as LOG-002 and
    # made even the single-value case look like a violation.
    shapes = load_shapes_graph(config.DEFAULT_SHAPES_DIR)
    registry = Registry.load(str(config.DEFAULT_REGISTRY_PATH))
    _conforms, rows, _text = run_shacl_native_rows(_graph(body), shapes, registry)
    found = {(r.focus_node, r.path) for r in rows if r.check_id == "LOG-002"}
    assert len(found) == expected, f"{case_id}: {sorted(found)}"


def test_both_arms_merge_into_one_row(tmp_path):
    """End to end: a real violation and a sameAs false positive in one graph.
    The violation is reported once with `sources=shacl+sparql`, which is the
    evidence that the two formulations agreed rather than one covering for
    the other."""
    from ontology_suite import pipeline
    from ontology_suite.checks.registry import Registry

    data = tmp_path / "data.ttl"
    data.write_text(HEADER + """
        ex:custodian a owl:FunctionalProperty .
        ex:abbey ex:foundedBy ex:aelfric , ex:aelfricOfEynsham .
        ex:aelfric owl:sameAs ex:aelfricOfEynsham .
        ex:abbey ex:custodian ex:trustA , ex:trustB .
    """, encoding="utf-8")

    out = tmp_path / "out"
    stage = pipeline.run_checks_stage(
        Registry.load(str(config.DEFAULT_REGISTRY_PATH)), out,
        ontology_path=None, data_path=str(data),
        shapes_dir=str(config.DEFAULT_SHAPES_DIR),
        sparql_dir=[str(config.DEFAULT_SPARQL_DIR)],
        inference="none", engine="both",
    )
    rows = [r for r in stage.rows if r.check_id == "LOG-002"]
    assert len(rows) == 1, [(r.focus_node, r.path, r.sources) for r in rows]
    assert rows[0].path.endswith("custodian"), rows[0].path
    assert set(rows[0].sources) == {"shacl", "sparql"}, rows[0].sources
