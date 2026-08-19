"""Compares two versions of an OWL2 ontology and classifies the change using
a semantic-versioning-style heuristic (MAJOR/MINOR/PATCH/NONE).

This is deliberately a heuristic, not a formal compatibility proof -- same
spirit as ``reasoning/profile.py``'s OWL2 profile checker and the rest of
this suite's lint-style checks. See ``docs/VERSIONING.md`` for the exact
rules and their rationale. In short:

  MAJOR (breaking) -- anything a consumer relying on the old ontology could
    break on: a class/property was removed, a subclass edge was removed
    (with both classes still present), a new disjointness axiom was added, a
    property's domain/range was narrowed (or changed incomparably), a
    property gained a new *restricting* characteristic (Functional /
    InverseFunctional / Asymmetric / Irreflexive), or an equivalentClass/
    equivalentProperty axiom was removed.

  MINOR (additive, backward-compatible) -- new classes/properties, new
    subclass edges, widened domain/range, a *relaxed* (removed) property
    characteristic, a newly added equivalence axiom, or a removed
    disjointness axiom -- provided nothing above triggered MAJOR.

  PATCH -- no structural change, but at least one term's rdfs:label or
    rdfs:comment text differs between the two versions.

  NONE -- no difference at all (by the facts this module tracks).

Scope: only the axiom forms ``ontology_evaluation.py`` and the registry
checks already track are compared -- named classes/properties,
rdfs:subClassOf edges between named classes, rdfs:domain/range,
owl:disjointWith, owl:equivalentClass/equivalentProperty, and the seven OWL2
property characteristics. Anonymous class expressions (owl:Restriction,
unionOf/intersectionOf/oneOf/complementOf) are not compared term-by-term;
a change purely inside one of those is invisible to this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Set, Tuple

from rdflib import Graph, OWL, RDF, RDFS
from rdflib.term import URIRef

from .. import hierarchy

PROPERTY_TYPES = (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property)
CHARACTERISTIC_TYPES = (
    OWL.FunctionalProperty, OWL.InverseFunctionalProperty, OWL.TransitiveProperty,
    OWL.SymmetricProperty, OWL.AsymmetricProperty, OWL.ReflexiveProperty, OWL.IrreflexiveProperty,
)
RESTRICTING_CHARACTERISTICS = {
    OWL.FunctionalProperty, OWL.InverseFunctionalProperty, OWL.AsymmetricProperty, OWL.IrreflexiveProperty,
}
ANNOTATION_PREDICATES = (RDFS.label, RDFS.comment)


class BumpLevel(str, Enum):
    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


@dataclass
class OntologySnapshot:
    """The structural facts extracted from one ontology graph, at a point in time."""
    classes: Set[URIRef] = field(default_factory=set)
    properties: Set[URIRef] = field(default_factory=set)
    subclass_edges: Set[Tuple[URIRef, URIRef]] = field(default_factory=set)
    disjoint_pairs: Set[FrozenSet] = field(default_factory=set)
    equivalent_classes: Set[FrozenSet] = field(default_factory=set)
    equivalent_properties: Set[FrozenSet] = field(default_factory=set)
    domain: Dict[URIRef, FrozenSet] = field(default_factory=dict)
    range: Dict[URIRef, FrozenSet] = field(default_factory=dict)
    characteristics: Dict[URIRef, FrozenSet] = field(default_factory=dict)
    annotations: Dict[URIRef, FrozenSet] = field(default_factory=dict)


def _named(term):
    """True for a named (URIRef) term, false for a blank node.

    Blank-node identifiers are never stable across two independent parses of
    "the same" anonymous class expression (owl:Restriction, unionOf,
    intersectionOf, ...) -- rdflib mints them fresh from parser-internal
    counters, so comparing them by identity across old/new snapshots would
    make every anonymous expression look "removed" and a lookalike "added"
    even when semantically identical. This suite already scopes anonymous
    class expressions out of the named class hierarchy elsewhere (see
    ontology_evaluation.py's DIT/NOC/NAC scope note); the version-diff tool
    follows the same convention rather than attempting blank-node-aware
    graph isomorphism matching.
    """
    return isinstance(term, URIRef)


def snapshot(graph: Graph) -> OntologySnapshot:
    """Extract the comparable structural facts from one ontology graph.

    Only named (URIRef) classes/properties/values are tracked throughout --
    see ``_named``'s docstring for why blank nodes are out of scope.
    """
    classes = {c for c in graph.subjects(RDF.type, OWL.Class) if _named(c)} | {
        c for c in graph.subjects(RDF.type, RDFS.Class) if _named(c)
    }
    properties: Set[URIRef] = set()
    for kind in PROPERTY_TYPES:
        properties |= {p for p in graph.subjects(RDF.type, kind) if _named(p)}

    subclass_edges = {
        (child, parent) for child, parent in graph.subject_objects(RDFS.subClassOf)
        if _named(child) and _named(parent)
    }
    for child, parent in subclass_edges:
        classes.add(child)
        classes.add(parent)

    disjoint_pairs = {
        frozenset((a, b)) for a, b in graph.subject_objects(OWL.disjointWith) if a != b and _named(a) and _named(b)
    }
    equivalent_classes = {
        frozenset((a, b)) for a, b in graph.subject_objects(OWL.equivalentClass) if a != b and _named(a) and _named(b)
    }
    equivalent_properties = {
        frozenset((a, b)) for a, b in graph.subject_objects(OWL.equivalentProperty)
        if a != b and _named(a) and _named(b)
    }

    domain: Dict[URIRef, set] = {}
    for p, c in graph.subject_objects(RDFS.domain):
        if _named(p) and _named(c):
            domain.setdefault(p, set()).add(c)
            properties.add(p)
    range_: Dict[URIRef, set] = {}
    for p, c in graph.subject_objects(RDFS.range):
        if _named(p) and _named(c):
            range_.setdefault(p, set()).add(c)
            properties.add(p)

    characteristics: Dict[URIRef, set] = {}
    for characteristic in CHARACTERISTIC_TYPES:
        for p in graph.subjects(RDF.type, characteristic):
            if _named(p):
                characteristics.setdefault(p, set()).add(characteristic)
                properties.add(p)

    annotations: Dict[URIRef, FrozenSet] = {}
    for term in classes | properties:
        entries = set()
        for pred in ANNOTATION_PREDICATES:
            for value in graph.objects(term, pred):
                entries.add((pred, str(value)))
        annotations[term] = frozenset(entries)

    return OntologySnapshot(
        classes=classes,
        properties=properties,
        subclass_edges=subclass_edges,
        disjoint_pairs=disjoint_pairs,
        equivalent_classes=equivalent_classes,
        equivalent_properties=equivalent_properties,
        domain={p: frozenset(v) for p, v in domain.items()},
        range={p: frozenset(v) for p, v in range_.items()},
        characteristics={p: frozenset(v) for p, v in characteristics.items()},
        annotations=annotations,
    )


@dataclass
class OntologyDiff:
    added_classes: Set[URIRef] = field(default_factory=set)
    removed_classes: Set[URIRef] = field(default_factory=set)
    added_properties: Set[URIRef] = field(default_factory=set)
    removed_properties: Set[URIRef] = field(default_factory=set)
    added_subclass_edges: Set[Tuple[URIRef, URIRef]] = field(default_factory=set)
    removed_subclass_edges: Set[Tuple[URIRef, URIRef]] = field(default_factory=set)
    added_disjoint_pairs: Set[FrozenSet] = field(default_factory=set)
    removed_disjoint_pairs: Set[FrozenSet] = field(default_factory=set)
    added_equivalent_classes: Set[FrozenSet] = field(default_factory=set)
    removed_equivalent_classes: Set[FrozenSet] = field(default_factory=set)
    added_equivalent_properties: Set[FrozenSet] = field(default_factory=set)
    removed_equivalent_properties: Set[FrozenSet] = field(default_factory=set)
    narrowed_domain: Dict[URIRef, Tuple[FrozenSet, FrozenSet]] = field(default_factory=dict)
    widened_domain: Dict[URIRef, Tuple[FrozenSet, FrozenSet]] = field(default_factory=dict)
    narrowed_range: Dict[URIRef, Tuple[FrozenSet, FrozenSet]] = field(default_factory=dict)
    widened_range: Dict[URIRef, Tuple[FrozenSet, FrozenSet]] = field(default_factory=dict)
    added_characteristics: Dict[URIRef, FrozenSet] = field(default_factory=dict)
    removed_characteristics: Dict[URIRef, FrozenSet] = field(default_factory=dict)
    changed_annotation_terms: Set[URIRef] = field(default_factory=set)


def _children_map(subclass_edges) -> Dict[URIRef, Set[URIRef]]:
    children: Dict[URIRef, Set[URIRef]] = {}
    for child, parent in subclass_edges:
        children.setdefault(parent, set()).add(child)
    return children


# Iterative, in ``ontology_suite/hierarchy.py`` -- the recursive form here
# cost one frame per subclass link and threw RecursionError at 1000, and
# a version diff is exactly the place a machine-generated hierarchy shows
# up. The memo-seeding cycle guard is preserved there verbatim.
_descendants_inclusive = hierarchy.descendants_inclusive


def _expand_with_subclasses(classes: FrozenSet[URIRef], children: Dict[URIRef, Set[URIRef]]) -> FrozenSet[URIRef]:
    """A domain/range class set, expanded to include every (transitive)
    subclass of each member -- since typing a subject as a subclass of a
    domain class still satisfies that domain (rdfs:subClassOf entailment),
    this is the actual set of types that satisfy the constraint, which is
    what widening/narrowing needs to compare rather than the bare IRIs."""
    memo: dict = {}
    expanded: Set[URIRef] = set()
    for cls in classes:
        expanded |= _descendants_inclusive(cls, children, memo)
    return frozenset(expanded)


def _classify_domain_range_change(
    old: FrozenSet, new: FrozenSet, old_expanded: FrozenSet, new_expanded: FrozenSet
) -> Optional[str]:
    """Classify how a property's domain (or range) changed between versions.

    An empty set means "no domain/range declared", i.e. unconstrained -- so
    going from empty to non-empty is a narrowing (a new constraint applies
    where none did before), not a widening, even though the empty set is
    technically a subset of everything. Otherwise, classes are compared via
    their *subclass-expanded* form (``old_expanded``/``new_expanded``), so
    e.g. moving a domain from a subclass to its superclass is recognized as
    a widening rather than an "incomparable" change.
    """
    if old == new:
        return None
    if not old and new:
        return "narrowed"
    if old and not new:
        return "widened"
    if new_expanded >= old_expanded:
        return "widened"
    if old_expanded >= new_expanded:
        return "narrowed"
    return "changed"  # incomparable: some old values dropped, some new added


def compute_diff(old: OntologySnapshot, new: OntologySnapshot) -> OntologyDiff:
    diff = OntologyDiff(
        added_classes=new.classes - old.classes,
        removed_classes=old.classes - new.classes,
        added_properties=new.properties - old.properties,
        removed_properties=old.properties - new.properties,
        added_subclass_edges=new.subclass_edges - old.subclass_edges,
        removed_subclass_edges=old.subclass_edges - new.subclass_edges,
        added_disjoint_pairs=new.disjoint_pairs - old.disjoint_pairs,
        removed_disjoint_pairs=old.disjoint_pairs - new.disjoint_pairs,
        added_equivalent_classes=new.equivalent_classes - old.equivalent_classes,
        removed_equivalent_classes=old.equivalent_classes - new.equivalent_classes,
        added_equivalent_properties=new.equivalent_properties - old.equivalent_properties,
        removed_equivalent_properties=old.equivalent_properties - new.equivalent_properties,
    )

    # Domain/range/characteristics are only compared for properties that
    # exist in *both* versions -- a brand-new or fully-removed property's
    # domain/range is not a separate "narrowing"/"widening" signal, it's
    # already captured by added_properties/removed_properties above.
    common_properties = old.properties & new.properties

    old_children = _children_map(old.subclass_edges)
    new_children = _children_map(new.subclass_edges)

    for p in common_properties:
        old_d, new_d = old.domain.get(p, frozenset()), new.domain.get(p, frozenset())
        kind = _classify_domain_range_change(
            old_d, new_d, _expand_with_subclasses(old_d, old_children), _expand_with_subclasses(new_d, new_children)
        )
        if kind in ("narrowed", "changed"):
            diff.narrowed_domain[p] = (old_d, new_d)
        elif kind == "widened":
            diff.widened_domain[p] = (old_d, new_d)

        old_r, new_r = old.range.get(p, frozenset()), new.range.get(p, frozenset())
        kind = _classify_domain_range_change(
            old_r, new_r, _expand_with_subclasses(old_r, old_children), _expand_with_subclasses(new_r, new_children)
        )
        if kind in ("narrowed", "changed"):
            diff.narrowed_range[p] = (old_r, new_r)
        elif kind == "widened":
            diff.widened_range[p] = (old_r, new_r)

        old_chars = old.characteristics.get(p, frozenset())
        new_chars = new.characteristics.get(p, frozenset())
        added = new_chars - old_chars
        removed = old_chars - new_chars
        if added:
            diff.added_characteristics[p] = added
        if removed:
            diff.removed_characteristics[p] = removed

    common_terms = (old.classes | old.properties) & (new.classes | new.properties)
    diff.changed_annotation_terms = {
        term for term in common_terms
        if old.annotations.get(term, frozenset()) != new.annotations.get(term, frozenset())
    }

    return diff


def classify_bump(diff: OntologyDiff) -> BumpLevel:
    """Classify ``diff`` into a semantic-versioning-style bump level. See the
    module docstring for the exact rules."""
    major = (
        bool(diff.removed_classes)
        or bool(diff.removed_properties)
        or bool(diff.removed_subclass_edges)
        or bool(diff.added_disjoint_pairs)
        or bool(diff.narrowed_domain)
        or bool(diff.narrowed_range)
        or bool(diff.removed_equivalent_classes)
        or bool(diff.removed_equivalent_properties)
        or any(chars & RESTRICTING_CHARACTERISTICS for chars in diff.added_characteristics.values())
    )
    if major:
        return BumpLevel.MAJOR

    minor = (
        bool(diff.added_classes)
        or bool(diff.added_properties)
        or bool(diff.added_subclass_edges)
        or bool(diff.widened_domain)
        or bool(diff.widened_range)
        or bool(diff.added_characteristics)
        or bool(diff.removed_characteristics)
        or bool(diff.added_equivalent_classes)
        or bool(diff.added_equivalent_properties)
        or bool(diff.removed_disjoint_pairs)
    )
    if minor:
        return BumpLevel.MINOR

    if diff.changed_annotation_terms:
        return BumpLevel.PATCH

    return BumpLevel.NONE


def diff_ontologies(old_graph: Graph, new_graph: Graph) -> Tuple[OntologyDiff, BumpLevel]:
    """Convenience entry point: snapshot both graphs, diff them, classify the bump."""
    diff = compute_diff(snapshot(old_graph), snapshot(new_graph))
    return diff, classify_bump(diff)


def _sorted_str(items) -> list:
    return sorted(str(i) for i in items)


def _fmt_set_pair(pair: Tuple[FrozenSet, FrozenSet]) -> str:
    old, new = pair
    return f"{{{', '.join(_sorted_str(old)) or '(none)'}}} -> {{{', '.join(_sorted_str(new)) or '(none)'}}}"


def format_report(diff: OntologyDiff, bump: BumpLevel, old_label: str = "old", new_label: str = "new") -> str:
    """A human-readable text report of ``diff``, ending in the suggested bump."""
    lines = [f"Ontology version diff: {old_label} -> {new_label}", ""]

    def section(title, items, fmt=str):
        if not items:
            return
        lines.append(f"{title}:")
        for item in sorted(items, key=str) if not isinstance(items, dict) else sorted(items.items(), key=lambda kv: str(kv[0])):
            if isinstance(items, dict):
                k, v = item
                lines.append(f"  - {k}: {fmt(v)}")
            else:
                lines.append(f"  - {fmt(item)}")
        lines.append("")

    section("Removed classes [MAJOR]", diff.removed_classes)
    section("Removed properties [MAJOR]", diff.removed_properties)
    section("Removed subclass edges [MAJOR]", diff.removed_subclass_edges, lambda e: f"{e[0]} no longer rdfs:subClassOf {e[1]}")
    section("New disjointness axioms [MAJOR]", diff.added_disjoint_pairs, lambda p: " disjointWith ".join(_sorted_str(p)))
    section("Narrowed/changed property domains [MAJOR]", diff.narrowed_domain, _fmt_set_pair)
    section("Narrowed/changed property ranges [MAJOR]", diff.narrowed_range, _fmt_set_pair)
    section("Removed equivalentClass axioms [MAJOR]", diff.removed_equivalent_classes, lambda p: " equivalentClass ".join(_sorted_str(p)))
    section("Removed equivalentProperty axioms [MAJOR]", diff.removed_equivalent_properties, lambda p: " equivalentProperty ".join(_sorted_str(p)))
    section(
        "New restricting property characteristics [MAJOR]",
        {p: c & RESTRICTING_CHARACTERISTICS for p, c in diff.added_characteristics.items() if c & RESTRICTING_CHARACTERISTICS},
        lambda c: ", ".join(_sorted_str(c)),
    )

    section("Added classes [minor]", diff.added_classes)
    section("Added properties [minor]", diff.added_properties)
    section("Added subclass edges [minor]", diff.added_subclass_edges, lambda e: f"{e[0]} rdfs:subClassOf {e[1]}")
    section("Widened property domains [minor]", diff.widened_domain, _fmt_set_pair)
    section("Widened property ranges [minor]", diff.widened_range, _fmt_set_pair)
    section("Removed (relaxed) disjointness axioms [minor]", diff.removed_disjoint_pairs, lambda p: " disjointWith ".join(_sorted_str(p)))
    section("Added equivalentClass axioms [minor]", diff.added_equivalent_classes, lambda p: " equivalentClass ".join(_sorted_str(p)))
    section("Added equivalentProperty axioms [minor]", diff.added_equivalent_properties, lambda p: " equivalentProperty ".join(_sorted_str(p)))
    section("Removed (relaxed) property characteristics [minor]", diff.removed_characteristics, lambda c: ", ".join(_sorted_str(c)))

    section("Terms with changed rdfs:label/rdfs:comment only [patch]", diff.changed_annotation_terms)

    lines.append(f"Suggested version bump: {bump.value.upper()}")
    return "\n".join(lines)


def to_json(diff: OntologyDiff, bump: BumpLevel) -> dict:
    """A machine-readable equivalent of ``format_report``."""
    def strs(items):
        return _sorted_str(items)

    def pair(items: Dict[URIRef, Tuple[FrozenSet, FrozenSet]]):
        return {str(k): {"old": _sorted_str(v[0]), "new": _sorted_str(v[1])} for k, v in items.items()}

    def frozensets(items: Set[FrozenSet]):
        return [_sorted_str(fs) for fs in items]

    def chars(items: Dict[URIRef, FrozenSet]):
        return {str(k): _sorted_str(v) for k, v in items.items()}

    return {
        "bump": bump.value,
        "removed_classes": strs(diff.removed_classes),
        "added_classes": strs(diff.added_classes),
        "removed_properties": strs(diff.removed_properties),
        "added_properties": strs(diff.added_properties),
        "removed_subclass_edges": [[str(a), str(b)] for a, b in diff.removed_subclass_edges],
        "added_subclass_edges": [[str(a), str(b)] for a, b in diff.added_subclass_edges],
        "added_disjoint_pairs": frozensets(diff.added_disjoint_pairs),
        "removed_disjoint_pairs": frozensets(diff.removed_disjoint_pairs),
        "added_equivalent_classes": frozensets(diff.added_equivalent_classes),
        "removed_equivalent_classes": frozensets(diff.removed_equivalent_classes),
        "added_equivalent_properties": frozensets(diff.added_equivalent_properties),
        "removed_equivalent_properties": frozensets(diff.removed_equivalent_properties),
        "narrowed_domain": pair(diff.narrowed_domain),
        "widened_domain": pair(diff.widened_domain),
        "narrowed_range": pair(diff.narrowed_range),
        "widened_range": pair(diff.widened_range),
        "added_characteristics": chars(diff.added_characteristics),
        "removed_characteristics": chars(diff.removed_characteristics),
        "changed_annotation_terms": strs(diff.changed_annotation_terms),
    }
