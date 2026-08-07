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
    where ``node`` is the subject, recursing one level into any blank-node
    object so multi-triple structures (restrictions, RDF lists) aren't cut
    in half."""
    seen = _seen if _seen is not None else set()
    if node in seen:
        return
    seen.add(node)
    for predicate, obj in graph.predicate_objects(node):
        yield (node, predicate, obj)
        if isinstance(obj, BNode):
            yield from concise_bounded_description(graph, obj, seen)


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
