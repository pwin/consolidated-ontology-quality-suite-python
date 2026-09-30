"""The holosdb engine the portable checks run on, and the claims made for it.

Every assertion here corresponds to a sentence in
`checks/holos_sparql.py`'s docstrings. The point is that the reasons for
moving the portable path off rdflib are measured rather than asserted, and
that if holosdb ever became lenient in the way rdflib is, a test would say so
rather than a user finding out.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from rdflib import BNode, Graph, Literal, URIRef

from ontology_suite.checks.holos_sparql import HolosSession, QueryFailed

EX = "https://example.org/holos/"

#: The STY-003 shape, reduced to the part under test: a label with no language
#: tag, on whatever carries it.
FIXTURE = textwrap.dedent(
    """
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex:   <https://example.org/holos/> .

    ex:Car rdfs:subClassOf [ a owl:Restriction ; rdfs:label "has a wheel" ] .
    ex:Chassis rdfs:label "Chassis" .
    """
)

UNTAGGED_LABELS = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
CONSTRUCT {
  _:r a sh:ValidationResult ;
    sh:focusNode ?e ;
    sh:value ?l ;
    sh:resultMessage ?msg .
}
WHERE {
  ?e rdfs:label ?l .
  FILTER(LANG(?l) = "")
  %s
  BIND(CONCAT("Label '", STR(?l), "' on ", ?focusText) AS ?msg)
}
"""

#: The fix that shipped: IF() evaluates only the branch it takes, so STR() is
#: never reached for a blank node.
GUARDED = UNTAGGED_LABELS % 'BIND(IF(isBlank(?e), "[a blank node]", STR(?e)) AS ?focusText)'
#: What was there before, and what this engine is expected to refuse.
UNGUARDED = UNTAGGED_LABELS % "BIND(STR(?e) AS ?focusText)"

SH_MESSAGE = URIRef("http://www.w3.org/ns/shacl#resultMessage")
SH_FOCUS = URIRef("http://www.w3.org/ns/shacl#focusNode")


@pytest.fixture()
def graph() -> Graph:
    g = Graph()
    g.parse(data=FIXTURE, format="turtle")
    return g


@pytest.fixture()
def session(graph: Graph):
    with HolosSession(graph) as s:
        yield s


def test_the_transfer_moves_every_triple(graph: Graph, session: HolosSession) -> None:
    """A transfer that silently drops triples would make every check under-report,
    and would look like the checks getting quieter rather than like a bug."""
    assert session.quads == len(graph)


def test_the_transfer_carries_datatypes_and_language_tags(graph: Graph) -> None:
    """N-Triples is the interchange, so anything the checks filter on has to
    survive it -- `LANG()` and `DATATYPE()` are what most of them test."""
    g = Graph()
    subject = URIRef(EX + "s")
    g.add((subject, URIRef(EX + "tagged"), Literal("hello", lang="en")))
    g.add((subject, URIRef(EX + "typed"), Literal("12x", datatype=URIRef(
        "http://www.w3.org/2001/XMLSchema#integer"))))
    g.add((subject, URIRef(EX + "plain"), Literal("bare")))
    with HolosSession(g) as s:
        assert s.quads == 3
        rows = s.construct(
            "CONSTRUCT { ?s <urn:lang> ?t } WHERE { ?s <%stagged> ?o BIND(LANG(?o) AS ?t) }"
            % EX
        )
        assert [str(o) for _s, _p, o in rows] == ["en"]
        rows = s.construct(
            "CONSTRUCT { ?s <urn:dt> ?d } WHERE { ?s <%styped> ?o BIND(DATATYPE(?o) AS ?d) }"
            % EX
        )
        assert [str(o) for _s, _p, o in rows] == [
            "http://www.w3.org/2001/XMLSchema#integer"
        ]


def test_a_blank_node_focus_still_gets_a_message(session: HolosSession) -> None:
    """The defect this engine move was made for, from the reader's side: both
    labels are untagged, so both must be reported *and* say something."""
    results = session.construct(GUARDED)
    messages = [str(o) for _s, _p, o in results.triples((None, SH_MESSAGE, None))]
    assert len(messages) == 2, results.serialize(format="nt")
    assert all(messages), "a finding with no message"
    assert any("[a blank node]" in m for m in messages)
    assert any("Chassis" in m for m in messages)


def test_str_of_a_blank_node_is_a_type_error_here(session: HolosSession) -> None:
    """SPARQL 1.1 17.4.2.5 defines STR() for literals and IRIs, so this must
    lose the blank node's message and keep the IRI's.

    Pinned deliberately. rdflib returns the internal blank node identifier
    instead of raising, which is exactly how `STY-003` passed its tests here
    for so long while shipping broken through the extension. If holosdb ever
    became lenient the same way, this is the test that would say so -- and the
    guard in every affected check would quietly stop being load-bearing.
    """
    results = session.construct(UNGUARDED)
    messages = [str(o) for _s, _p, o in results.triples((None, SH_MESSAGE, None))]
    assert len(messages) == 1
    assert "Chassis" in messages[0]


def test_one_result_keeps_its_triples_on_one_subject(session: HolosSession) -> None:
    """A CONSTRUCT template's blank node is one node per solution, and the
    seven triples of a `sh:ValidationResult` have to stay together or
    `merge._extract_rows` reads half a finding."""
    results = session.construct(GUARDED)
    subjects = set(results.subjects(SH_MESSAGE, None))
    assert len(subjects) == 2
    for subject in subjects:
        assert len(list(results.predicate_objects(subject))) == 4
        assert len(list(results.objects(subject, SH_FOCUS))) == 1


def test_blank_node_labels_are_not_the_input_labels(graph: Graph) -> None:
    """Why `merge._anonymous_key` exists, stated as a test.

    holosdb relabels blank nodes when it parses -- labels in a document are
    document-scoped and an engine may rename them, and pyoxigraph does the
    same. So a blank node's label here is not comparable with the label
    pyshacl reports for the same node, and the dedup key cannot use it.
    """
    g = Graph()
    original = BNode("deliberately_stable")
    g.add((original, URIRef(EX + "p"), Literal("x")))
    with HolosSession(g) as s:
        rows = s.construct("CONSTRUCT { ?s <urn:q> ?o } WHERE { ?s <%sp> ?o }" % EX)
        subjects = [x for x in rows.subjects(None, None)]
        assert len(subjects) == 1
        assert isinstance(subjects[0], BNode)
        assert str(subjects[0]) != str(original)


def test_an_empty_result_is_an_empty_graph(session: HolosSession) -> None:
    assert len(session.construct(
        "CONSTRUCT { ?s <urn:q> ?o } WHERE { ?s <urn:nothing> ?o }"
    )) == 0


def test_a_select_is_refused_rather_than_read_as_no_findings(session: HolosSession) -> None:
    """"Produced nothing" and "was not a CONSTRUCT" are different facts, and a
    caller pointing the runner at its own query tree needs to be told which."""
    with pytest.raises(QueryFailed, match="CONSTRUCT"):
        session.construct("SELECT ?s WHERE { ?s ?p ?o }")


def test_a_broken_query_reports_the_engine_message(session: HolosSession) -> None:
    with pytest.raises(QueryFailed):
        session.construct("CONSTRUCT { ?s ?p ?o } WHERE { this is not sparql }")


def test_a_closed_session_stops_answering(graph: Graph) -> None:
    """An in-memory store is cheap, but a persistent one holds an exclusive
    lock on its directory, and `close()` is what the bindings document as the
    way not to wait for the garbage collector."""
    session = HolosSession(graph)
    session.close()
    with pytest.raises(Exception):
        session.construct(GUARDED)


def test_holosdb_sees_a_boolean_lexical_form_rdflib_has_already_lost(tmp_path: Path) -> None:
    """The `DAT-001` difference, and the reason `HolosSession` cannot recover
    it on the pipeline's behalf.

    rdflib's `NORMALIZE_LITERALS` rewrites an invalid `xsd:boolean` to
    `"false"` while parsing, so the authored lexical form is gone from the
    graph before any SPARQL expression can see it -- and therefore gone from
    what `HolosSession` is handed. Reading the file directly, holosdb keeps
    it. This is measured here so the limit is a known one with a test on it
    rather than a surprise, and so the claim in `_transfer` stays true.
    """
    source = tmp_path / "boolean.ttl"
    source.write_text(
        '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n'
        '<%(ex)sa> <%(ex)sisActive> "yesplz"^^xsd:boolean .\n' % {"ex": EX},
        encoding="utf-8",
    )

    lexical = (
        'CONSTRUCT { ?s <urn:lex> ?form } WHERE { ?s <%sisActive> ?o '
        'BIND(STR(?o) AS ?form) }' % EX
    )

    # Straight from the file: the authored form survives.
    import holosdb

    store = holosdb.Store()
    store.load(str(source))
    try:
        direct = store.query(lexical)
    finally:
        store.close()
    assert len(direct) == 1
    assert '"yesplz"' in direct[0], direct

    # Via rdflib, which is the route the pipeline takes: already normalised.
    graph = Graph()
    graph.parse(str(source), format="turtle")
    with HolosSession(graph) as session:
        through_rdflib = [
            str(o) for _s, _p, o in session.construct(lexical)
        ]
    assert through_rdflib == ["false"], (
        "rdflib stopped normalising invalid booleans -- if that is deliberate, "
        "DAT-001 can now report this case through the pipeline and "
        "checks/literal_typing.py's native coverage is redundant"
    )
