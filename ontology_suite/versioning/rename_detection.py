"""Heuristically pairs up an :mod:`ontology_suite.versioning.diff` result's
``removed_classes``/``added_classes`` (and properties) into probable
renames -- the same term, either given a new local name, moved to a new
namespace, or both.

This exists to bridge version-diff and TARQL/oxi-gen alignment: a
:class:`TermRename` is the concrete fact ``repair.tarql_repair`` needs to
turn an "undeclared class/property" finding (from
``sketch.prefix_alignment.check_undeclared_terms``) into an actual suggested
substitution in the query file, instead of just an ontology-declaration
stub. Not every removed/added pair is a rename (some really are unrelated
additions/removals); this is a heuristic ranked by confidence, same spirit
as the rest of this suite's lint-style checks -- see
``versioning/diff.py``'s own module docstring.

Two independent signals are used, in priority order:

1. **Explicit migration annotation** (``owl:equivalentClass``/
   ``owl:equivalentProperty``/``dcterms:isReplacedBy``, directly in the new
   ontology graph) -- always confidence 1.0 when present. This is the only
   signal that can catch a genuine *semantic* rename (``ex:Widget`` ->
   ``ex:Product``, unrelated spellings) -- an ontology author who wants
   downstream tooling (this one included) to resolve the rename
   automatically should leave exactly this kind of tombstone behind when
   retiring the old IRI. ``owl:equivalentClass``/``owl:equivalentProperty``
   are logically symmetric, so *either* direction is recognized -- asserted
   from the old (removed) IRI to the new (added) one, or the other way
   round (asserting it from the new term -- "here's what this replaces" --
   is at least as natural to write). ``dcterms:isReplacedBy`` is directional
   by definition and only recognized old -> new, as its semantics require.
2. **Local-name similarity** (identical, or a close ``difflib`` match) --
   the fallback when no explicit annotation exists. This only catches
   renames that kept a similar spelling (typo fixes, capitalization, a
   namespace-only bump keeping the same local name); it cannot infer a
   rename between two unrelated words on structure alone -- there is no
   general way to distinguish "Widget was renamed to Product" from "Widget
   was removed and Product, unrelated, was added" without signal #1 or a
   human. Findings from ``sketch.prefix_alignment.check_undeclared_terms``
   remain the ground truth either way; this module only ever *sharpens* a
   subset of them into a rename fix instead of an ontology-stub fix (see
   ``repair.tarql_repair``'s module docstring) -- it never suppresses a
   finding.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List, Optional, Set

from rdflib import OWL, Graph
from rdflib.namespace import Namespace
from rdflib.term import URIRef

from .diff import OntologyDiff

MIN_LOCAL_NAME_SIMILARITY = 0.6

DCTERMS = Namespace("http://purl.org/dc/terms/")
# (label, symmetric?). owl:equivalentClass/equivalentProperty are logically
# symmetric -- a reasoner treats "A equivalentClass B" and "B equivalentClass
# A" identically -- so both directions are recognized as a migration
# tombstone regardless of which side an author asserts it from (asserting it
# *from the new term* -- "here's what this replaces" -- is at least as
# natural as from the old one). dcterms:isReplacedBy is directional by
# definition (the *subject* is replaced by the *object*) and stays one-way;
# checking its reverse would nonsensically claim the new term is replaced by
# the old one.
_MIGRATION_PREDICATES = {
    OWL.equivalentClass: ("owl:equivalentClass", True),
    OWL.equivalentProperty: ("owl:equivalentProperty", True),
    DCTERMS.isReplacedBy: ("dcterms:isReplacedBy", False),
}


@dataclass(frozen=True)
class TermRename:
    kind: str  # "class" | "property"
    old_iri: str
    new_iri: str
    confidence: float  # 1.0 = exact local-name match (same or different namespace); lower = fuzzy
    reason: str

    @property
    def namespace_changed(self) -> bool:
        return _split_iri(self.old_iri)[0] != _split_iri(self.new_iri)[0]

    @property
    def local_name_changed(self) -> bool:
        return _split_iri(self.old_iri)[1] != _split_iri(self.new_iri)[1]


def _split_iri(iri: str) -> tuple:
    """(namespace, local_name), same '#'-then-'/' convention used throughout this suite."""
    iri = str(iri)
    for sep in ("#", "/"):
        if sep in iri:
            head, _, tail = iri.rpartition(sep)
            return head + sep, tail
    return "", iri


def _explicit_migration_candidates(
    new_graph: Optional[Graph], removed: Set[URIRef], added: Set[URIRef]
) -> List[tuple]:
    if new_graph is None:
        return []
    candidates = []
    for predicate, (label, symmetric) in _MIGRATION_PREDICATES.items():
        for subject, obj in new_graph.subject_objects(predicate):
            if subject in removed and obj in added:
                candidates.append((1.0, subject, obj, f"explicit {label} assertion in the new ontology"))
            elif symmetric and obj in removed and subject in added:
                candidates.append((1.0, obj, subject, f"explicit {label} assertion (reverse direction) in the new ontology"))
    return candidates


def _match_renames(
    removed: Set[URIRef], added: Set[URIRef], kind: str, new_graph: Optional[Graph] = None
) -> List[TermRename]:
    if not removed or not added:
        return []

    candidates = list(_explicit_migration_candidates(new_graph, removed, added))
    for old in removed:
        old_local = _split_iri(old)[1]
        for new in added:
            new_local = _split_iri(new)[1]
            if old_local == new_local:
                candidates.append((1.0, old, new, "identical local name"))
                continue
            ratio = difflib.SequenceMatcher(a=old_local, b=new_local).ratio()
            if ratio >= MIN_LOCAL_NAME_SIMILARITY:
                candidates.append((ratio * 0.85, old, new, f"similar local name ({ratio:.0%} match)"))

    # Explicit-annotation candidates sort first among confidence-1.0 ties (list order is stable),
    # so a real tombstone assertion is preferred over a coincidental identical-local-name match.
    candidates.sort(key=lambda c: c[0], reverse=True)
    used_old: Set[URIRef] = set()
    used_new: Set[URIRef] = set()
    renames: List[TermRename] = []
    for confidence, old, new, reason in candidates:
        if old in used_old or new in used_new:
            continue
        used_old.add(old)
        used_new.add(new)
        renames.append(TermRename(kind=kind, old_iri=str(old), new_iri=str(new), confidence=confidence, reason=reason))
    return sorted(renames, key=lambda r: (-r.confidence, r.old_iri))


def detect_renames(diff: OntologyDiff, new_graph: Optional[Graph] = None) -> List[TermRename]:
    """Best-effort 1:1 pairing of removed<->added classes and properties.
    Each removed/added term is used in at most one pairing (greedy,
    highest-confidence-first assignment, explicit-annotation matches always
    winning ties); leftover removed/added terms with no plausible match are
    simply not returned -- they're a genuine removal or genuine addition,
    not a rename. Pass `new_graph` (the same graph `diff` was computed
    against as the "new" side) to also check for explicit migration
    annotations -- see the module docstring; without it, only the
    local-name-similarity signal is used.
    """
    return (
        _match_renames(diff.removed_classes, diff.added_classes, "class", new_graph)
        + _match_renames(diff.removed_properties, diff.added_properties, "property", new_graph)
    )
