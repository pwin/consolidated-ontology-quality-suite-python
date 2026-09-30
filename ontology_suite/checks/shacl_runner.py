"""Shapes loading, and pyshacl kept as an independent second opinion.

`load_shapes_graph` is the suite's only way of reading `shapes/*.ttl` and is
used by every engine.

`run_shacl` is **not** in the runtime path any more. The SHACL formulation
runs on the `shacl` engine (https://github.com/pwin/SHACL_Engine) --
`shacl_native_runner.py` -- and pyshacl is a *development* dependency, in the
same role pyoxigraph plays for SPARQL: an implementation with no code in
common with ours, whose job is to disagree with us when we are wrong. That is
not a rhetorical benefit. It is what
`tests/test_engine_parity_stress.py` and `tests/test_shacl_native_runner.py`
compare against, and it has caught real native-engine bugs -- the
`isIRI($this)` blank-node regression fixed in `shacl` 0.1.5, and the two
blank-node cases recorded in the former's docstring.

So: pwin's engines do the work, third-party engines check it. Removing pyshacl
entirely would remove the evidence that the engine doing the work is right.

``advanced=True`` is required so that pyshacl evaluates the SHACL-SPARQL
extensions (``sh:sparql`` SPARQLConstraintComponent and ``sh:target`` of
type ``sh:SPARQLTarget``) used throughout shapes/*.ttl.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rdflib import Graph

try:
    from pyshacl import validate as _pyshacl_validate
except ImportError as exc:  # pragma: no cover
    _pyshacl_validate = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def load_shapes_graph(shapes_dir: str | Path) -> Graph:
    """Load every .ttl file under shapes_dir into a single shapes graph."""
    g = Graph()
    shapes_dir = Path(shapes_dir)
    for path in sorted(shapes_dir.glob("*.ttl")):
        g.parse(path, format="turtle")
    return g


def run_shacl(
    data_graph: Graph,
    shapes_graph: Graph,
    ont_graph: Graph | None = None,
    inference: str = "none",
) -> tuple[bool, Graph, str]:
    """Run pyshacl validation and return (conforms, results_graph, results_text).

    For tests only -- see this module's docstring. Nothing the suite ships
    calls this, and pyshacl is not a runtime dependency, so a caller reaching
    for it in production gets the `RuntimeError` below rather than a silent
    second SHACL implementation.

    ``inference`` may be 'none', 'rdfs', 'owlrl' or 'both' as supported by
    pyshacl; 'none' is the safe default so the suite reports on the graph
    exactly as authored rather than on inferred closure. Note that the engine
    the suite actually uses accepts only 'none' and 'rdfs', so a parity test
    comparing the two has to stay inside that subset.
    """
    if _pyshacl_validate is None:
        raise RuntimeError(
            "pyshacl is not installed. It is a development dependency, for the "
            "engine-parity tests only -- `uv sync` installs it. The suite's own "
            "SHACL engine is `shacl`; see this module's docstring."
        ) from _IMPORT_ERROR

    conforms, results_graph, results_text = _pyshacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        ont_graph=ont_graph,
        inference=inference,
        advanced=True,
        allow_infos=True,
        allow_warnings=True,
        debug=False,
    )
    return conforms, results_graph, results_text
