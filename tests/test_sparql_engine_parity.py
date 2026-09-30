"""Every portable SPARQL check, run by two SPARQL engines, asserted to agree.

`test_engine_parity_stress.py` next door compares two *SHACL* implementations
-- pyshacl against the native `shacl` engine -- for the checks that have both
a shape and a `.rq` twin. This compares two *SPARQL* engines over the `.rq`
files themselves, which is a different axis and catches a different class of
bug: a check that leans, without meaning to, on something one engine does
leniently and the specification does not require.

rdflib is the engine the suite ships on. pyoxigraph is the second opinion: a
separate implementation (Rust, the Oxigraph family) with no code in common
with rdflib's Python evaluator. Skipped entirely if it is not installed, the
way the SHACL parity test skips without `shacl` -- add it with
`uv add --dev pyoxigraph`.

# What this found when it was written

Two disagreements over the 44 shipped checks, on
`examples/checks_stress_test/`:

1. `STY-003` built its `sh:resultMessage` with `STR(?e)`, and 60 of its 65
   focus nodes on that fixture are blank nodes -- anonymous restrictions and
   axioms carrying an `rdfs:label`. SPARQL 1.1 17.4.2.5 defines `STR()` for
   literals and IRIs, so `STR()` of a blank node is a type error: `CONCAT`
   errors, `BIND` leaves `?msg` unbound, and the template drops the message.
   rdflib does not raise -- it returns the internal blank node identifier --
   so under rdflib the message was present but named something meaningless
   and unstable between runs, and under pyoxigraph 60 of 65 messages were
   simply missing. Fixed in the query with `COALESCE(STR(?e), ...)`.

   The same `STR(?focus)`-inside-`CONCAT` shape is in 29 of the 44 checks.
   Only `STY-003` is exposed by the current fixtures, because only its focus
   nodes are blank there; the rest would start disagreeing the moment a
   fixture gives them a blank node. That is what this test is for.

2. `DAT-001`'s boolean branch, which is a known and already-documented
   rdflib blind spot rather than a defect here -- see
   `checks/literal_typing.py`, which covers it natively because rdflib
   rewrites an invalid `xsd:boolean` to `"false"` at parse time and no
   SPARQL expression can see the authored lexical form afterwards. It is
   listed in `KNOWN_ENGINE_DIFFERENCES` below rather than silently tolerated,
   so that the exemption is visible and has a reason attached.

# Why the comparison is on constructed triples

Each check is a `CONSTRUCT` producing `sh:ValidationResult` triples, and the
count of them is what a caller acts on. Comparing the triples as *sets* is
not possible: every result is hung off a fresh blank node, so the two engines
label them differently by design. So this compares how many triples each
engine produced, per predicate -- which is what caught `STY-003`, where the
subject count matched at 65 and only `sh:resultMessage` differed.
"""
from __future__ import annotations

import collections
import warnings
from pathlib import Path

import pytest
from rdflib import Graph

from ontology_suite import config

pyoxigraph = pytest.importorskip(
    "pyoxigraph",
    reason="the second SPARQL engine is optional: uv add --dev pyoxigraph",
)

#: Fixtures to run every check against. The stress pair is deliberately
#: repetitive -- several instances of each flaw -- which is what makes a
#: disagreement show up as a count rather than as a single missing row.
FIXTURES = {
    "stress": (
        "examples/checks_stress_test/stress-ontology.ttl",
        "examples/checks_stress_test/stress-data.ttl",
    ),
    "gist-core": ("examples/vehicle/gistCore14.1.0.ttl",),
}

#: Differences that are understood, with the reason. Anything not listed here
#: is a failure: the point of the test is that an unexplained divergence stops
#: the build rather than being absorbed.
KNOWN_ENGINE_DIFFERENCES = {
    # rdflib's xsd:boolean converter never raises. It warns and yields False,
    # and with NORMALIZE_LITERALS on (the default) the stored lexical form is
    # re-serialized from that value, so "yesplz"^^xsd:boolean is stored as
    # "false" -- which matches DAT-001's own regex. The authored lexical form
    # is gone from the rdflib object entirely, so no SPARQL expression can
    # recover it. checks/literal_typing.py covers this case natively.
    "data/DAT-001.rq": "rdflib normalises an invalid xsd:boolean at parse time",
}

REPO = Path(__file__).resolve().parent.parent
CHECKS = sorted(config.DEFAULT_SPARQL_DIR.rglob("*.rq"))


def _relative(check: Path) -> str:
    return check.relative_to(config.DEFAULT_SPARQL_DIR).as_posix()


def _by_predicate(triples) -> dict[str, int]:
    """How many triples each predicate carries.

    Per predicate rather than a single total, because a total can match while
    the shape of the answer differs -- and per *predicate* is what localises a
    disagreement to the one template line that caused it.
    """
    # The angle brackets are stripped because the two engines render an IRI
    # differently -- pyoxigraph in N-Triples form, `<http://...>`, and rdflib
    # bare -- so comparing the rendered strings reported every check with any
    # result as a difference. Twenty-seven phantom failures, before this line.
    return dict(
        sorted(
            collections.Counter(str(p).strip("<>") for _s, p, _o in triples).items()
        )
    )


def _rdflib_counts(graph: Graph, query: str) -> dict[str, int]:
    constructed = Graph()
    for triple in graph.query(query):
        constructed.add(triple)
    return _by_predicate(constructed)


def _oxigraph_counts(store, query: str) -> dict[str, int]:
    return _by_predicate(
        (t.subject, t.predicate, t.object) for t in store.query(query)
    )


@pytest.fixture(scope="module", params=sorted(FIXTURES), ids=sorted(FIXTURES))
def loaded(request):
    """One fixture, loaded into both engines once and shared by every check.

    Module-scoped because parsing it per check would dominate the run and
    measure the parsers rather than the queries.
    """
    paths = [REPO / p for p in FIXTURES[request.param]]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        pytest.skip(f"fixture missing: {missing}")

    graph = Graph()
    store = pyoxigraph.Store()
    with warnings.catch_warnings():
        # The stress fixture contains deliberately invalid literals, and
        # rdflib warns about each one on load. That is the fixture working.
        warnings.simplefilter("ignore")
        for path in paths:
            graph.parse(str(path))
            store.load(path=str(path), format=pyoxigraph.RdfFormat.TURTLE)
    return graph, store


@pytest.mark.parametrize("check", CHECKS, ids=[_relative(c) for c in CHECKS])
def test_both_engines_construct_the_same_results(check: Path, loaded) -> None:
    graph, store = loaded
    query = check.read_text(encoding="utf-8")
    name = _relative(check)

    rdflib_counts = _rdflib_counts(graph, query)
    oxigraph_counts = _oxigraph_counts(store, query)

    if rdflib_counts == oxigraph_counts:
        # A known difference that has stopped happening is worth knowing about:
        # the exemption should be removed rather than left to rot.
        return

    reason = KNOWN_ENGINE_DIFFERENCES.get(name)
    if reason:
        pytest.xfail(f"{name}: known difference -- {reason}")

    predicates = sorted(set(rdflib_counts) | set(oxigraph_counts))
    detail = "\n".join(
        f"    {p}: rdflib {rdflib_counts.get(p, 0)}, pyoxigraph {oxigraph_counts.get(p, 0)}"
        for p in predicates
        if rdflib_counts.get(p, 0) != oxigraph_counts.get(p, 0)
    )
    pytest.fail(
        f"{name} produces different results under the two SPARQL engines.\n"
        f"  Per predicate, where they differ:\n{detail}\n"
        f"  A check that depends on one engine's leniency is a check that will\n"
        f"  behave differently the moment the engine changes. The usual causes:\n"
        f"    * STR() of a blank node -- a type error in SPARQL, tolerated by\n"
        f"      rdflib. Wrap it: COALESCE(STR(?x), \"[a blank node]\").\n"
        f"    * a UNION branch holding only a FILTER or BIND and no triple\n"
        f"      pattern, which matches nothing under rdflib.\n"
        f"  If the difference is understood and cannot be fixed in the query,\n"
        f"  add it to KNOWN_ENGINE_DIFFERENCES with the reason."
    )


def test_the_known_difference_list_names_real_checks() -> None:
    """An exemption for a check that no longer exists hides a gap rather than
    documenting one."""
    known = set(KNOWN_ENGINE_DIFFERENCES)
    actual = {_relative(c) for c in CHECKS}
    assert known <= actual, f"exempted checks that do not exist: {sorted(known - actual)}"
