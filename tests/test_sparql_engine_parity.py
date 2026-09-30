"""Every portable SPARQL check, run by three SPARQL engines, asserted to agree.

`test_engine_parity_stress.py` next door compares two *SHACL* implementations
-- pyshacl against the native `shacl` engine -- for the checks that have both
a shape and a `.rq` twin. This compares SPARQL *engines* over the `.rq` files
themselves, which is a different axis and catches a different class of bug: a
check that leans, without meaning to, on something one engine does leniently
and the specification does not require.

holosdb is the engine the checks run on (`checks/holos_sparql.py`), so it is
the reference here rather than one opinion among equals. The others are
independent implementations with no code in common with it:

* **rdflib**, which the suite still uses to parse and to hold results, and
  which the portable checks used to run on.
* **pyoxigraph**, the Oxigraph family -- which is what the VS Code extension
  ships (as oxigraph for JS), so a difference showing up here is a difference
  users of the extension would get. Optional; skipped if not installed.

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
   and unstable between runs, while under holosdb and pyoxigraph 60 of 65
   messages were simply missing. Fixed in the query with
   `IF(isBlank(?e), "[a blank node]", STR(?e))`, which produces the same text
   in all three because it never calls `STR()` on a blank node at all.

   The same `STR(?focus)`-inside-`CONCAT` shape is in 29 of the 44 checks.
   Only `STY-003` and `EFF-001` are exposed by the current fixtures, because
   only their focus nodes are blank there; the rest would start disagreeing
   the moment a fixture gives them a blank node. That is what this test is
   for.

2. `DAT-001`'s boolean branch. This one no longer reads as a mutual
   difference, and that is the point of holosdb being the reference:
   rdflib's `NORMALIZE_LITERALS` rewrites an invalid `xsd:boolean` to
   `"false"` while parsing, so the authored lexical form is gone before any
   SPARQL expression can see it. holosdb and pyoxigraph both keep it and
   report the third bad literal on the stress fixture; rdflib reports two of
   three. It is listed in `KNOWN_ENGINE_DIFFERENCES` below rather than
   silently tolerated, so the exemption is visible and has a reason attached
   -- and it is recorded as a limitation of the engine that cannot see the
   data, not as a disagreement about what the check means.

# Why the comparison is on constructed triples

Each check is a `CONSTRUCT` producing `sh:ValidationResult` triples, and the
count of them is what a caller acts on. Comparing the triples as *sets* is
not possible: every result is hung off a fresh blank node, and the engines
label those differently by design -- see
`test_holos_sparql.test_blank_node_labels_are_not_the_input_labels`. So this
compares how many triples each engine produced, per predicate -- which is what
caught `STY-003`, where the subject count matched at 65 and only
`sh:resultMessage` differed.

# Why each engine loads the fixture file itself

Rather than one engine parsing and handing the graph to the others. Parsing is
part of what is being compared: `DAT-001` is a difference that exists *only*
at parse time, and routing every engine through rdflib's parser would hide it
by normalising the literal before any of them saw it. `test_holos_sparql.py`
covers the other direction -- what survives when the pipeline does hand a
parsed rdflib graph across.
"""
from __future__ import annotations

import collections
import re
import warnings
from pathlib import Path

import holosdb
import pytest
from rdflib import Graph

from ontology_suite import config

pyoxigraph = pytest.importorskip(
    "pyoxigraph",
    reason="the third SPARQL engine is optional: uv add --dev pyoxigraph",
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

#: Differences that are understood, with the reason, keyed by
#: `(check, engine)`. Anything not listed here is a failure: the point of the
#: test is that an unexplained divergence stops the build rather than being
#: absorbed.
KNOWN_ENGINE_DIFFERENCES = {
    # rdflib's xsd:boolean converter never raises. It warns and yields False,
    # and with NORMALIZE_LITERALS on (the default) the stored lexical form is
    # re-serialized from that value, so an invalid boolean is stored as
    # "false" -- which is a *valid* boolean and so matches nothing DAT-001 is
    # looking for. The authored lexical form is gone from the rdflib object
    # entirely, so no SPARQL expression can recover it. holosdb reports 3 bad
    # literals on the stress fixture where rdflib reports 2.
    # checks/literal_typing.py covers this case natively, which is what makes
    # the suite's own answer complete regardless of engine.
    ("data/DAT-001.rq", "rdflib"): "rdflib normalises an invalid xsd:boolean at parse time",
}

REPO = Path(__file__).resolve().parent.parent
CHECKS = sorted(config.DEFAULT_SPARQL_DIR.rglob("*.rq"))

#: Enough N-Triples to pull the predicate out of a line holosdb returns. The
#: bindings hand back one string per triple, with no trailing separator.
NTRIPLE = re.compile(r"^(?:<[^>]*>|_:\S+)\s+<([^>]+)>\s+")


def _relative(check: Path) -> str:
    return check.relative_to(config.DEFAULT_SPARQL_DIR).as_posix()


def _counted(predicates) -> dict[str, int]:
    """How many triples each predicate carries.

    Per predicate rather than a single total, because a total can match while
    the shape of the answer differs -- and per *predicate* is what localises a
    disagreement to the one template line that caused it.
    """
    return dict(sorted(collections.Counter(predicates).items()))


def _rdflib_counts(graph: Graph, query: str) -> dict[str, int]:
    # The angle brackets are stripped because the engines render an IRI
    # differently -- N-Triples form against rdflib's bare string -- so
    # comparing the rendered strings reported every check with any result as a
    # difference. Twenty-seven phantom failures, before this line.
    return _counted(str(p).strip("<>") for _s, p, _o in graph.query(query))


def _oxigraph_counts(store, query: str) -> dict[str, int]:
    return _counted(str(t.predicate).strip("<>") for t in store.query(query))


def _holos_counts(store, query: str) -> dict[str, int]:
    predicates = []
    for line in store.query(query):
        match = NTRIPLE.match(line.strip())
        assert match, f"could not read a predicate out of {line!r}"
        predicates.append(match.group(1))
    return _counted(predicates)


@pytest.fixture(scope="module", params=sorted(FIXTURES), ids=sorted(FIXTURES))
def loaded(request):
    """One fixture, loaded into all three engines once and shared by every
    check.

    Module-scoped because parsing it per check would dominate the run and
    measure the parsers rather than the queries.
    """
    paths = [REPO / p for p in FIXTURES[request.param]]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        pytest.skip(f"fixture missing: {missing}")

    graph = Graph()
    oxigraph = pyoxigraph.Store()
    holos = holosdb.Store()
    with warnings.catch_warnings():
        # The stress fixture contains deliberately invalid literals, and
        # rdflib warns about each one on load. That is the fixture working.
        warnings.simplefilter("ignore")
        for path in paths:
            graph.parse(str(path))
            oxigraph.load(path=str(path), format=pyoxigraph.RdfFormat.TURTLE)
            holos.load(str(path))
    try:
        yield graph, oxigraph, holos
    finally:
        holos.close()


@pytest.mark.parametrize("check", CHECKS, ids=[_relative(c) for c in CHECKS])
def test_the_other_engines_agree_with_the_one_we_ship(check: Path, loaded) -> None:
    graph, oxigraph, holos = loaded
    query = check.read_text(encoding="utf-8")
    name = _relative(check)

    reference = _holos_counts(holos, query)
    others = {
        "rdflib": _rdflib_counts(graph, query),
        "pyoxigraph": _oxigraph_counts(oxigraph, query),
    }

    failures = []
    for engine, counts in others.items():
        if counts == reference:
            # A known difference that has stopped happening is worth knowing
            # about: the exemption should be removed rather than left to rot.
            continue
        if KNOWN_ENGINE_DIFFERENCES.get((name, engine)):
            continue
        predicates = sorted(set(reference) | set(counts))
        detail = "\n".join(
            f"      {p}: holosdb {reference.get(p, 0)}, {engine} {counts.get(p, 0)}"
            for p in predicates
            if reference.get(p, 0) != counts.get(p, 0)
        )
        failures.append(f"    against {engine}:\n{detail}")

    if not failures:
        return

    pytest.fail(
        f"{name} produces different results under different SPARQL engines.\n"
        + "\n".join(failures)
        + "\n  holosdb is what the suite runs the checks on, so a difference here\n"
        "  is a check that behaves one way in this repo and another in the VS\n"
        "  Code extension, which ships these same files to oxigraph. The usual\n"
        "  causes:\n"
        "    * STR() of a blank node -- a type error in SPARQL, tolerated by\n"
        "      rdflib. Guard it with IF(isBlank(?x), ..., STR(?x)), which is\n"
        "      stronger than COALESCE because it never calls STR() on the\n"
        "      blank node at all.\n"
        "    * a UNION branch holding only a FILTER or BIND and no triple\n"
        "      pattern, which matches nothing under rdflib.\n"
        "  If the difference is understood and cannot be fixed in the query,\n"
        "  add (check, engine) to KNOWN_ENGINE_DIFFERENCES with the reason."
    )


def test_the_known_difference_list_names_real_checks() -> None:
    """An exemption for a check that no longer exists hides a gap rather than
    documenting one."""
    known = {check for check, _engine in KNOWN_ENGINE_DIFFERENCES}
    actual = {_relative(c) for c in CHECKS}
    assert known <= actual, f"exempted checks that do not exist: {sorted(known - actual)}"


def test_the_engine_the_suite_ships_is_the_one_being_used() -> None:
    """The reference above is only meaningful if it is also what runs in
    anger. If the runner were ever pointed back at rdflib, every assertion in
    this file would still pass while measuring nothing."""
    from ontology_suite.checks import sparql_runner

    assert sparql_runner.HolosSession.__module__.endswith("holos_sparql")
