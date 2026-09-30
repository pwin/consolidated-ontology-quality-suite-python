"""The SPARQL engine the portable checks run on: holosdb.

The `.rq` checks are the *portable* formulation -- self-contained CONSTRUCT
queries producing standard ``sh:ValidationResult`` triples, needing a SPARQL
engine and no SHACL processor. They used to run on rdflib, because rdflib was
already in the process for parsing. They now run on holosdb (the HOLOS
triplestore's Python bindings), which is a conformant SPARQL 1.2 engine, and
rdflib keeps the jobs it is actually needed for: parsing, pyshacl, and holding
the results graph the rest of the suite consumes.

# Why move off rdflib

rdflib is lenient in ways the specification does not license, and a check that
leans on that leniency behaves differently the moment it runs anywhere else --
which it does, because the VS Code extension ships these same query files to
oxigraph. Two measured cases:

1. ``STR()`` of a blank node. SPARQL 1.1 17.4.2.5 defines ``STR()`` for
   literals and IRIs, so this is a type error: ``CONCAT`` errors, ``BIND``
   leaves the variable unbound, and the CONSTRUCT template drops the triple.
   rdflib does not raise -- it returns the internal blank node identifier --
   so ``STY-003`` looked healthy here while losing its message for 60 of 65
   findings under any conformant engine, and shipped that way in the
   extension. A check can no longer pass here and fail in the field.

2. ``xsd:boolean`` lexical forms. rdflib's ``NORMALIZE_LITERALS`` rewrites
   ``"yesplz"^^xsd:boolean`` to ``"false"`` at parse time, so the authored
   lexical form is gone before any SPARQL expression can see it and
   ``DAT-001`` cannot report it. Measured on
   ``examples/checks_stress_test``: rdflib finds 2 bad literals, holosdb
   finds 3. See `_transfer` for what this module can and cannot recover.

# What changes in the output

Blank node labels. Both holosdb and oxigraph relabel blank nodes on load --
labels in a parsed document are document-scoped and an engine is free to
rename, which pyoxigraph does too -- and holosdb's are fresh per query
execution. So a blank node's ``sh:focusNode`` label is not comparable with the
label pyshacl reports for the same node, and `checks/merge.py` deduplicates on
a key that includes the focus node. A finding with a blank-node focus and both
a shape and a `.rq` twin is therefore reported once per formulation rather than
merged. rdflib's own labels were already unstable between processes, so nothing
that was reproducible has stopped being so.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from rdflib import BNode, Graph, URIRef

import holosdb


def _is_rdf(triple) -> bool:
    """Whether a triple is RDF, as opposed to *generalised* RDF.

    RDF 1.1 requires a subject to be an IRI or a blank node and a predicate to
    be an IRI. rdflib's in-memory graph does not enforce either, and an OWL-RL
    closure exploits that freely: `owlrl` derives `owl:sameAs` reflexivity and
    `rdf:type` membership over *literals*, so on the vehicle fixture 1,773 of
    the closure's 10,176 triples have a literal subject.

    No conformant engine can hold those -- holosdb refuses the document, and so
    would the oxigraph the extension ships -- and no check wants them: a
    pattern like `?s rdf:type ?t` matching a literal subject is exactly the
    post-closure flooding `tests/test_vehicle_gist_checks.py` exists to keep
    out. So they are dropped on the way in, and counted, because a silent drop
    of 17% of a graph is not something to find out about later.
    """
    subject, predicate, _object = triple
    return isinstance(subject, (URIRef, BNode)) and isinstance(predicate, URIRef)


class QueryFailed(RuntimeError):
    """A check did not execute. Carries the engine's message unchanged."""


class HolosSession:
    """One in-memory holosdb store holding the graph under test.

    Built once per run and queried by every check. The transfer is the
    expensive part of this class, so doing it per check would measure the
    loader rather than the queries -- on the stress fixture that is 44
    re-parses of the same data.

    A context manager, because an unclosed store holds its engine until the
    garbage collector gets round to it.
    """

    def __init__(self, graph: Graph) -> None:
        self._store = holosdb.Store()
        self._skipped = 0
        self._quads = self._transfer(graph)

    def _transfer(self, graph: Graph) -> int:
        """Serialise the rdflib graph into holosdb, via N-Triples on disk.

        The bindings load from a path, so this writes a temporary file rather
        than passing a buffer. The file is N-Triples because it is the format
        both sides agree on exactly and streams line by line.

        Triples that are not RDF are left out -- see `_is_rdf`, and `skipped`
        for how many.

        This is also the limit on the ``DAT-001`` improvement described in the
        module docstring: a literal that rdflib normalised at *parse* time is
        already normalised in `graph`, and serialising it cannot bring the
        authored form back. holosdb reports the third bad literal only when it
        reads the source file itself, which is what
        `tests/test_holos_sparql.py` measures directly. Recovering it in the
        pipeline needs `rdflib.NORMALIZE_LITERALS` off at load, which is a
        change to what every other stage sees and is deliberately not made
        here.
        """
        # Counted before anything is copied, so a graph that is already RDF --
        # every graph the suite loads from a file -- pays one pass and no
        # second copy of itself.
        generalised = sum(1 for triple in graph if not _is_rdf(triple))
        self._skipped = generalised
        if generalised:
            usable = Graph()
            for triple in graph:
                if _is_rdf(triple):
                    usable.add(triple)
            payload = usable.serialize(format="nt")
        else:
            payload = graph.serialize(format="nt")

        with tempfile.TemporaryDirectory(prefix="oqs-holos-") as tmp:
            path = Path(tmp) / "graph.nt"
            path.write_text(payload, encoding="utf-8")
            return self._store.load(str(path))

    @property
    def quads(self) -> int:
        """Quads the engine holds. Compared against ``len(graph)`` by the
        tests, so a transfer that drops triples for any reason other than
        `skipped` is a failure rather than a quiet undercount."""
        return self._quads

    @property
    def skipped(self) -> int:
        """Triples left behind because they were not RDF. See `_is_rdf`.

        Zero for anything parsed from a file. Non-zero only downstream of a
        reasoner that emits generalised RDF, and then it is worth surfacing
        rather than discovering as a finding count that moved."""
        return self._skipped

    def construct(self, query: str) -> Graph:
        """Run one CONSTRUCT and return its triples as an rdflib graph.

        The bindings return a list of N-Triples strings *without* the trailing
        separator, so it is added back here before parsing. Each query's
        output is parsed as its own document, which is what keeps the seven
        triples of one ``sh:ValidationResult`` hanging off one subject.
        """
        try:
            triples = self._store.query(query)
        except Exception as exc:  # noqa: BLE001 -- the caller records the text
            raise QueryFailed(str(exc)) from exc

        # An ASK returns a bool; a SELECT returns a list too, but of solution
        # objects rather than N-Triples strings, so the element type is what
        # actually distinguishes them. Every shipped check is a CONSTRUCT; a
        # caller pointing the runner at its own query tree might not be, and
        # "produced no results" would be the wrong way to say so.
        if not isinstance(triples, list) or any(
            not isinstance(triple, str) for triple in triples
        ):
            got = (
                type(triples[0]).__name__
                if isinstance(triples, list) and triples
                else type(triples).__name__
            )
            raise QueryFailed(f"expected a CONSTRUCT or DESCRIBE, got {got}")

        out = Graph()
        if not triples:
            return out
        lines: List[str] = [f"{t} ." for t in triples]
        out.parse(data="\n".join(lines), format="nt")
        return out

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "HolosSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
