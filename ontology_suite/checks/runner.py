"""
High-level entry point tying together shacl_runner, sparql_runner,
merge and registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rdflib import Graph

from .merge import ResultRow, build_unified_results
from .registry import Registry
from .shacl_runner import load_shapes_graph, run_shacl
from .sparql_runner import SparqlCheckOutcome, run_sparql_checks


@dataclass
class SuiteRun:
    rows: List[ResultRow]
    shacl_conforms: bool
    shacl_results_text: str
    sparql_outcomes: List[SparqlCheckOutcome]
    data_graph: Graph
    registry: Registry


def load_graph(*paths: str | Path) -> Graph:
    g = Graph()
    for p in paths:
        p = Path(p)
        fmt = "turtle"
        if p.suffix in (".nt",):
            fmt = "nt"
        elif p.suffix in (".jsonld",):
            fmt = "json-ld"
        elif p.suffix in (".rdf", ".xml", ".owl"):
            fmt = "xml"
        g.parse(p, format=fmt)
    return g


def run_suite(
    data_path: str | Path,
    ontology_path: Optional[str | Path] = None,
    shapes_dir: str | Path = "shapes",
    sparql_dir: str | Path = "sparql",
    registry_path: str | Path = "registry.json",
    inference: str = "none",
) -> SuiteRun:
    """Run the full suite and return a unified, deduplicated result set.

    The data graph and (optional) ontology graph are combined into a single
    working graph so that class/property declarations in the ontology can
    be checked against usage in the data, and vice versa. If you want to
    validate the ontology in isolation, simply omit ``data_path`` variety
    and pass the ontology itself as ``data_path``.
    """
    registry = Registry.load(registry_path)

    paths = [data_path]
    if ontology_path:
        paths.append(ontology_path)
    working_graph = load_graph(*paths)

    shapes_graph = load_shapes_graph(shapes_dir)

    conforms, shacl_results_graph, shacl_results_text = run_shacl(
        working_graph, shapes_graph, inference=inference
    )

    sparql_results_graph, sparql_outcomes = run_sparql_checks(working_graph, sparql_dir)

    rows = build_unified_results(
        shacl_results_graph, sparql_results_graph, registry, shapes_graph
    )

    return SuiteRun(
        rows=rows,
        shacl_conforms=conforms,
        shacl_results_text=shacl_results_text,
        sparql_outcomes=sparql_outcomes,
        data_graph=working_graph,
        registry=registry,
    )
