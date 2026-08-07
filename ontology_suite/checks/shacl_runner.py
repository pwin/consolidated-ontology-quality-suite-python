"""
Runs the SHACL half of the suite using pyshacl.

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

    ``inference`` may be 'none', 'rdfs', 'owlrl' or 'both' as supported by
    pyshacl; 'none' is the safe default so the suite reports on the graph
    exactly as authored rather than on inferred closure.
    """
    if _pyshacl_validate is None:
        raise RuntimeError(
            "pyshacl is not installed. Run `pip install -r requirements.txt`."
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
