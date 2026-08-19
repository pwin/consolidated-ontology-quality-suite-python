"""Sampling for the ``data`` pipeline stage: reduce a large data graph to a
tractable subset before running the comparatively expensive reasoning pass
(``consistency.run_consistency_checks``), while the cheap SPARQL/SHACL
registry checks still run over the full graph regardless.

Uses a Concise Bounded Description (CBD) of a random sample of named
subjects: every triple the subject appears in, plus one hop into any
blank-node object (so e.g. an ``owl:Restriction`` or list node attached to a
sampled resource comes along with it) -- the smallest sample that still lets
reasoning say something meaningful about each sampled node.
"""
from __future__ import annotations

import random
from typing import Iterator, List, Optional, Tuple

from rdflib import BNode, Graph, URIRef
from rdflib.term import Node


def sample_subjects(graph: Graph, n: int, seed: Optional[int] = None) -> List[URIRef]:
    """Return up to ``n`` named subjects from ``graph``, chosen uniformly at
    random (deterministically if ``seed`` is given). Returns every named
    subject, unchanged, if the graph already has ``n`` or fewer."""
    subjects = sorted({s for s in graph.subjects() if isinstance(s, URIRef)}, key=str)
    if len(subjects) <= n:
        return subjects
    return random.Random(seed).sample(subjects, n)


def concise_bounded_description(
    graph: Graph, node: Node, _seen: Optional[set] = None
) -> Iterator[Tuple[Node, Node, Node]]:
    """Yield every triple in ``node``'s Concise Bounded Description: triples
    where ``node`` is the subject, descending into any blank-node object so
    multi-triple structures (restrictions, RDF lists) aren't cut in half.

    Iterative, over an explicit frontier. The recursive form cost one Python
    frame -- two, being a generator delegated to with ``yield from`` -- per
    blank node in the chain, and an RDF collection is a *chain* of blank
    nodes: one ``rdf:rest`` cell per member. So the depth here is set by the
    longest list in the input, not by nesting, and any ``owl:oneOf``,
    ``owl:unionOf`` or plain collection past ~5,000 members raised
    ``RecursionError`` from a sampling helper (measured). ``docgen``'s own
    CBD in ``docgen/class_diagrams.py`` was already written this way; this
    one was the outlier.

    Traversal order differs from the recursive form's depth-first order.
    Both callers collect the triples into a set-semantics ``Graph``, so the
    resulting graph is identical either way.
    """
    seen = _seen if _seen is not None else set()
    frontier = [node]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for predicate, obj in graph.predicate_objects(current):
            yield (current, predicate, obj)
            if isinstance(obj, BNode) and obj not in seen:
                frontier.append(obj)


def sample_graph(graph: Graph, n: int, seed: Optional[int] = None) -> Graph:
    """Return a new graph holding the CBD of ``n`` randomly sampled named
    subjects from ``graph`` -- or an exact copy if it already has ``n`` or
    fewer named subjects."""
    sampled = Graph()
    for prefix, namespace in graph.namespaces():
        sampled.bind(prefix, namespace)
    for subject in sample_subjects(graph, n, seed):
        for triple in concise_bounded_description(graph, subject):
            sampled.add(triple)
    return sampled
