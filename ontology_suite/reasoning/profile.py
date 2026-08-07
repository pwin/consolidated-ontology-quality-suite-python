"""Heuristic OWL2 EL / QL / RL profile-membership checker.

The three OWL2 "tractable" profiles (EL, QL, RL -- see the W3C OWL2 Profiles
recommendation) each restrict the OWL2 DL grammar to a fragment with a
specific complexity guarantee (e.g. EL: existential-only, polynomial-time
classification via ELK; QL: first-order-rewritable query answering over a
database; RL: rule-based reasoning, which is exactly what this suite's own
``reasoning/backends/owlrl_backend.py`` implements). Whether an ontology
falls inside one of these fragments is a real, useful expressiveness signal
independent of whether it is logically consistent.

This is a **heuristic, syntactic** approximation, not a certified conformance
checker: it flags the well-known constructs each profile disallows (per the
grammar tables in the spec) by walking the asserted axioms directly. It does
not attempt full grammar-position analysis (e.g. QL's precise restrictions on
which side of a subclass axiom a construct may appear), and does not resolve
`owl:imports` itself -- pass in the already-merged graph (e.g. from
``ontology_evaluation.py``'s import resolution) if imports should count.
Treat a "no violations found" result as "no *known* violation found", not a
formal guarantee -- pair with a real profile checker (e.g. ELK for EL) before
relying on this for anything load-bearing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from rdflib import Graph, OWL, RDF, RDFS
from rdflib.term import BNode, URIRef

from ..checks.merge import ResultRow

_REGISTRY_ID = {"EL": "REA-010", "QL": "REA-011", "RL": "REA-012"}

RESTRICTION_FACETS = (
    "someValuesFrom", "allValuesFrom", "hasValue",
    "cardinality", "minCardinality", "maxCardinality",
    "qualifiedCardinality", "minQualifiedCardinality", "maxQualifiedCardinality",
    "hasSelf",
)


@dataclass
class ProfileViolation:
    profile: str  # "EL" | "QL" | "RL"
    construct: str
    subject: str
    message: str


@dataclass
class ProfileReport:
    violations: List[ProfileViolation] = field(default_factory=list)

    def exceeds(self, profile: str) -> bool:
        return any(v.profile == profile for v in self.violations)

    def by_profile(self) -> Dict[str, List[ProfileViolation]]:
        out: Dict[str, List[ProfileViolation]] = {"EL": [], "QL": [], "RL": []}
        for v in self.violations:
            out[v.profile].append(v)
        return out


def _restriction_facets(graph: Graph, node) -> List[str]:
    found = []
    for facet in RESTRICTION_FACETS:
        pred = URIRef(str(OWL) + facet)
        if (node, pred, None) in graph:
            found.append(facet)
    return found


def _cardinality_value(graph: Graph, node, facet: str):
    pred = URIRef(str(OWL) + facet)
    val = graph.value(node, pred)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _readable_subject(graph: Graph, node, _seen=None):
    """If ``node`` is a blank node (an inline class expression), walk
    backward to the nearest named resource it's part of the definition of,
    so report messages read as "ex:Person" rather than a raw blank-node id.
    Falls back to ``node`` itself if no named ancestor is found."""
    if not isinstance(node, BNode):
        return node
    _seen = _seen or set()
    if node in _seen:
        return node
    _seen.add(node)
    for s, _p, _o in graph.triples((None, None, node)):
        if isinstance(s, URIRef):
            return s
        return _readable_subject(graph, s, _seen)
    return node


ALL_PROFILES = ("EL", "QL", "RL")


def check_profiles(graph: Graph, profiles=ALL_PROFILES) -> ProfileReport:
    """Classify ``graph`` against the requested OWL2 profiles heuristically.

    ``profiles`` selects which of EL/QL/RL to actually check (default: all
    three) -- pass e.g. ``profiles=("EL",)`` to check only EL. An empty
    tuple does no work and returns an empty report; this is what
    ``pipeline.py``/the CLI default to, since most ontologies are
    deliberately full OWL2 DL and don't want unsolicited profile-violation
    noise on every run -- profile checking is opt-in via ``--profile``.

    Returns a ``ProfileReport`` whose ``violations`` list is empty for a
    profile the ontology appears to fit inside (or wasn't asked to check).
    """
    report = ProfileReport()
    wanted = set(profiles)
    if not wanted:
        return report

    def add(profile, construct, subject, message):
        if profile not in wanted:
            return
        report.violations.append(ProfileViolation(profile, construct, str(subject), message))

    # --- owl:unionOf anywhere: not in EL, not in QL, not in RL (as a
    # superclass-position disjunction) ---
    for cls in graph.subjects(OWL.unionOf, None):
        named = _readable_subject(graph, cls)
        add("EL", "unionOf", named, f"{named} is defined with owl:unionOf; EL permits no disjunction.")
        add("QL", "unionOf", named, f"{named} is defined with owl:unionOf; QL permits no disjunction.")
        add("RL", "unionOf", named, f"{named} is defined with owl:unionOf; RL disallows unionOf in a superclass position.")

    # --- owl:complementOf: not in EL, not in QL, not in RL ---
    for cls in graph.subjects(OWL.complementOf, None):
        named = _readable_subject(graph, cls)
        add("EL", "complementOf", named, f"{named} uses owl:complementOf (negation); EL has no negation.")
        add("QL", "complementOf", named, f"{named} uses owl:complementOf (negation); QL has no negation.")
        add("RL", "complementOf", named, f"{named} uses owl:complementOf (negation); RL has no negation.")

    # --- universal restrictions (owl:allValuesFrom): not in EL; RL allows it
    # only in a superclass (necessary-condition) position, which this
    # heuristic cannot distinguish, so it is flagged for awareness only under
    # EL/QL, not RL ---
    for r in graph.subjects(OWL.allValuesFrom, None):
        named = _readable_subject(graph, r)
        add("EL", "allValuesFrom", named, f"{named} is an allValuesFrom (universal) restriction; EL permits only existentials.")
        add("QL", "allValuesFrom", named, f"{named} is an allValuesFrom (universal) restriction; QL permits it only on the right of a subclass axiom, not verified here -- flagged for review.")

    # --- cardinality restrictions beyond 0/1: not in EL, not in QL, not in RL ---
    for r in graph.subjects(RDF.type, OWL.Restriction):
        named = _readable_subject(graph, r)
        for facet in ("cardinality", "minCardinality", "maxCardinality",
                      "qualifiedCardinality", "minQualifiedCardinality", "maxQualifiedCardinality"):
            n = _cardinality_value(graph, r, facet)
            if n is not None and n > 1:
                add("EL", facet, named, f"{named} has {facet}={n} (>1); EL only permits 0/1 cardinality.")
                add("QL", facet, named, f"{named} has {facet}={n} (>1); QL only permits 0/1 cardinality.")
                add("RL", facet, named, f"{named} has {facet}={n} (>1); RL only permits 0/1 cardinality.")

    # --- existential restrictions (owl:someValuesFrom) in a superclass
    # position: not in QL (QL only allows them in a subclass/domain position) ---
    subclass_objects = set(graph.objects(None, RDFS.subClassOf))
    for r in graph.subjects(OWL.someValuesFrom, None):
        if r in subclass_objects:
            named = _readable_subject(graph, r)
            add("QL", "someValuesFrom", named, f"{named} (a someValuesFrom restriction) is used as a superclass, which QL disallows.")

    # --- property characteristics QL/EL restrict or disallow ---
    for p in graph.subjects(RDF.type, OWL.TransitiveProperty):
        add("QL", "TransitiveProperty", p, f"{p} is owl:TransitiveProperty; QL disallows transitive properties.")
    for p in graph.subjects(RDF.type, OWL.FunctionalProperty):
        add("EL", "FunctionalProperty", p, f"{p} is owl:FunctionalProperty; EL disallows functional (data)properties in general position.")

    # --- disjointness: not in EL (only via a specific pairwise pattern QL/RL
    # do allow, but plain owl:disjointWith on classes is outside EL) ---
    for c1, c2 in graph.subject_objects(OWL.disjointWith):
        add("EL", "disjointWith", c1, f"{c1} owl:disjointWith {c2}; EL has no disjointness axioms.")

    # Deduplicate identical (profile, construct, subject) triples that may
    # have been added more than once via different graph walks.
    dedup = {}
    for v in report.violations:
        dedup[(v.profile, v.construct, v.subject)] = v
    report.violations = list(dedup.values())
    return report


def profile_report_to_rows(report: ProfileReport) -> List[ResultRow]:
    """Convert a ``ProfileReport`` into the same ``ResultRow`` shape every
    other check in this suite reports as, one row per violation, tagged with
    the registry id for the profile it violates (REA-010/011/012). These are
    Info-severity by convention (see registry.json) -- exceeding a profile is
    an expressiveness fact, not a defect."""
    rows: List[ResultRow] = []
    for v in report.violations:
        rows.append(ResultRow(
            check_id=_REGISTRY_ID[v.profile],
            category="reasoning",
            title=f"Ontology exceeds the OWL2 {v.profile} profile",
            severity="Info",
            focus_node=v.subject,
            path=None,
            value=v.construct,
            message=v.message,
            remediation=None,
            sources=["profile-checker"],
        ))
    return rows
