"""The shapes directory: parsed by rdflib, queried by holosdb, compiled by `shacl`.

WHY THIS MODULE EXISTS SEPARATELY FROM shacl_runner.py
------------------------------------------------------
`load_shapes_graph` and `declared_check_ids` used to live in
`shacl_runner.py`, whose job -- as its own docstring says -- is to keep pyshacl
around as an independent second opinion for the parity tests. pyshacl is a
*development* dependency, so that module opens with a guarded
``from pyshacl import validate``.

Nothing was broken by that: the guard catches the ImportError and a released
install runs fine. But it meant `pipeline.py`, `runner.py`, `cli.py` and
`examples/riverside_library/merge_shapes.py` all reached into the pyshacl
module to get at the shapes, so the runtime path's shape loading sat behind a
pyshacl import that worked only because someone had remembered to guard it.
One module whose docstring says "for tests only" was on every run's import
graph.

So the shapes live here, and `shacl_runner.py` is now only what it says it is.

WHICH ENGINE ANSWERS WHICH QUESTION
-----------------------------------
Three things happen to a shapes directory, and they are deliberately not done
by the same component:

* **Parsing** is rdflib's, and stays rdflib's. `holos_sparql.py`'s docstring
  draws this line explicitly -- rdflib keeps "the jobs it is actually needed
  for: parsing [...] and holding the results graph the rest of the suite
  consumes". `load_shapes_graph` is one of those jobs.

* **Querying** is holosdb's, since `9e6f99c` moved the checks off rdflib for
  the reason recorded in `holos_sparql.py`: rdflib is lenient where the
  specification is not, so an answer computed with rdflib's graph API is an
  answer about rdflib. `declared_check_ids` is a query -- "which check ids
  does this directory declare" -- and the number it returns is printed to a
  user and used in a warning, so it should come from the same engine stack the
  checks run on.

* **Accepting** is the `shacl` engine's, because it is the component that will
  actually run these shapes. rdflib parsing a file does not mean the engine
  will compile it, and `declared_check_ids` reporting 22 checks from a
  directory the engine rejects is a number about nothing. `engine_rejection`
  asks the engine, through the exact call `run_shacl_native` makes.
"""
from __future__ import annotations

from pathlib import Path

from rdflib import Graph

from .registry import OQ

#: Every check id the shapes declare, resolved the way `Registry.resolve_check_id`
#: resolves them: a shape's id is its local name unless it carries an
#: `oq:checkId` annotation naming the check it belongs to.
#:
#: A CONSTRUCT rather than a SELECT because `HolosSession` returns N-Triples
#: strings and a CONSTRUCT's output is a graph, so a shape with forty triples
#: contributes its (shape, id) pair exactly once with no DISTINCT needed.
#:
#: `isIRI(?shape)` is explicit rather than left to `STR()` erroring on a blank
#: node. The filter would do the right thing either way -- SPARQL 1.1 17.4.2.5
#: makes `STR()` of a blank node a type error, so the row would drop -- but
#: `STY-003` shipped broken for exactly that reason once (see
#: `holos_sparql.py`), and a check that relies on an error to get the right
#: answer reads as if it does not know it is relying on one.
_DECLARED_CHECK_IDS = f"""
PREFIX oq: <{OQ}>
CONSTRUCT {{ ?shape oq:checkId ?id }}
WHERE {{
  ?shape ?p ?o .
  FILTER(isIRI(?shape))
  FILTER(STRSTARTS(STR(?shape), "{OQ}"))
  OPTIONAL {{ ?shape oq:checkId ?annotated }}
  BIND(COALESCE(?annotated, STRAFTER(STR(?shape), "{OQ}")) AS ?id)
}}
"""


class ShapesRejected(ValueError):
    """The `shacl` engine will not compile the shapes it was given.

    Raised by the CLI rather than by `engine_rejection`, so that a script can
    ask the question without handling an exception, and carries the engine's
    message unchanged. A usage error, not a crash: the run cannot do anything
    with these shapes, and the reason is a line of text, not a stack.
    """


def load_shapes_graph(shapes_dir: str | Path) -> Graph:
    """Load every .ttl file under shapes_dir into a single shapes graph.

    The suite's only way of reading `shapes/*.ttl`, and used by every engine.
    The glob is deliberately non-recursive: a shapes *graph* is one directory,
    and a `shapes/archive/old.ttl` that silently joined it would be a shape
    nobody meant to run. See docs/EXTENDING.md.
    """
    g = Graph()
    shapes_dir = Path(shapes_dir)
    for path in sorted(shapes_dir.glob("*.ttl")):
        g.parse(path, format="turtle")
    return g


def declared_check_ids(shapes_dir: str | Path) -> set[str]:
    """The distinct check ids the shapes in ``shapes_dir`` declare.

    Answered by holosdb, over the graph rdflib parsed -- see this module's
    docstring for why the two steps belong to different engines.

    The resolution matches what a run will actually report, which is why the
    `oq:checkId` annotation is honoured: `STY-002-data` and `STY-002-obj` are
    two shapes for one check, and counting subjects alone says 23 where a run
    says 21.

    Used by the CLI to say how many checks a replaced shapes directory stopped
    running. Deliberately does not consult the registry: a project's own
    shapes name ids the shipped registry has never heard of, and they should
    be counted too.
    """
    # Imported here rather than at module scope so that `load_shapes_graph`,
    # which several callers want on its own, does not drag a triplestore into
    # the process to do a Turtle parse.
    from .holos_sparql import HolosSession

    graph = load_shapes_graph(shapes_dir)
    if not graph:
        return set()
    with HolosSession(graph) as session:
        return {str(obj) for _, _, obj in session.construct(_DECLARED_CHECK_IDS)}


def engine_rejection(shapes_dir: str | Path) -> str | None:
    """``None`` if the `shacl` engine compiles ``shapes_dir``, else its message.

    The question `declared_check_ids` cannot answer. rdflib will parse a file
    the engine then refuses -- an unsupported constraint component, a
    `sh:path` it cannot build -- and a merged directory that fails this way
    fails at validation time, several commands later, with nothing pointing
    back at the merge that produced it.

    Non-raising on purpose: the CLI reports it as a warning beside the others
    it already collects, and a script decides for itself whether to stop.
    Returns the message for a missing engine too, since "the engine would not
    load these" and "there is no engine" are both reasons not to trust a
    shape count.
    """
    from .shacl_native_runner import compile_shapes

    try:
        compile_shapes(load_shapes_graph(shapes_dir))
    except Exception as exc:  # noqa: BLE001 -- the engine's text is the answer
        return str(exc)
    return None
