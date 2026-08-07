"""Integration tests for the optional external OWL2 DL reasoner backend
(owlready2 + HermiT). These are real, slow (JVM-startup) integration tests
that actually invoke Java, not unit tests -- skipped entirely if owlready2
isn't installed (``uv sync --extra reasoner``) or the reasoner isn't
actually runnable in this environment (e.g. no Java runtime on PATH), via a
one-time smoke check rather than failing every test individually.

Two real bugs were caught getting this backend to actually run rather than
just gracefully report "unavailable":

1. ``external_backend.py`` loaded the serialized ontology via
   ``Path.as_uri()``, which owlready2 mishandles on Windows (it strips the
   ``file://`` scheme naively, leaving an invalid ``/C:/...`` path). Fixed
   by passing the plain filesystem path instead.
2. An inconsistent ontology makes ``owlready2.sync_reasoner()`` raise
   ``OwlReadyInconsistentOntologyError`` -- that was being caught by the
   generic ``except Exception`` and misreported as "reasoner unavailable"
   (``REA-022``) instead of the correct ``REA-020`` (ontology inconsistent).
"""
import pytest
from rdflib import Graph

from ontology_suite.reasoning.backends import external_backend

pytestmark = pytest.mark.skipif(
    not external_backend.available(), reason="owlready2 not installed (uv sync --extra reasoner)"
)

PREFIXES = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <https://example.org/hermit-test/> .
"""


def g(ttl: str) -> Graph:
    graph = Graph()
    graph.parse(data=PREFIXES + ttl, format="turtle")
    return graph


CONSISTENT = """
ex:Animal a owl:Class .
ex:Dog a owl:Class ; rdfs:subClassOf ex:Animal .
ex:Cat a owl:Class ; rdfs:subClassOf ex:Animal .
ex:Dog owl:disjointWith ex:Cat .
ex:rex a ex:Dog .
"""

INCONSISTENT = (
    CONSISTENT
    + """
ex:rex a ex:Cat .
"""
)


@pytest.fixture(scope="module")
def reasoner_actually_runs():
    """Skip every test in this module (with a clear reason) if HermiT can't
    actually run here, rather than failing each one individually."""
    rows = external_backend.run_external_reasoner(g(CONSISTENT), reasoner="hermit")
    if rows and rows[0].check_id == "REA-022":
        pytest.skip(f"external reasoner not actually runnable here: {rows[0].message}")


def test_consistent_ontology_reports_no_findings(reasoner_actually_runs):
    rows = external_backend.run_external_reasoner(g(CONSISTENT), reasoner="hermit")
    assert rows == []


def test_inconsistent_ontology_reports_rea020(reasoner_actually_runs):
    rows = external_backend.run_external_reasoner(g(INCONSISTENT), reasoner="hermit")
    ids = {r.check_id for r in rows}
    assert "REA-020" in ids


def test_unsupported_datatype_degrades_to_rea022_not_a_crash(reasoner_actually_runs):
    """xsd:date is not part of the OWL2 datatype map (only xsd:dateTime is),
    so a conformant reasoner like HermiT refuses to process it -- this
    should degrade to an informational REA-022, not raise."""
    ttl = """
ex:Animal a owl:Class .
ex:hasBirthDate a owl:DatatypeProperty ; rdfs:domain ex:Animal ; rdfs:range <http://www.w3.org/2001/XMLSchema#date> .
"""
    rows = external_backend.run_external_reasoner(g(ttl), reasoner="hermit")
    assert len(rows) == 1
    assert rows[0].check_id == "REA-022"
