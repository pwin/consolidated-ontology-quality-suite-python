"""Ontology-quality statistics for a Turtle file, aligned with the OQuaRE /
OntoQA metric *definitions* rather than graph_quality.py's raw-triple
approximations.

OQuaRE (Duque-Ramos et al.) and OntoQA (Tartir et al.) define their metrics
over an ontology's SCHEMA - its classes, properties and subclass hierarchy -
not over individual data triples. tarql CONSTRUCT templates almost never
declare an explicit TBox (no owl:Class / rdfs:domain / rdfs:range /
rdfs:subClassOf axioms), so this script first *induces* an implicit schema
from how the instance data actually uses classes and properties (preferring
any explicit axioms that ARE present), then computes the standard formulas
against that schema:

  Schema richness (Tartir 2005 OntoQA):
    AR - Attribute Richness    = attribute slots / number of classes
    RR - Relationship Richness = object properties / (object properties + subclass edges)
    IR - Inheritance Richness  = subclass edges / number of classes

  Instance richness (Tartir 2005 OntoQA):
    CR - Class Richness        = classes with >=1 instance / number of classes
    AP - Average Population    = typed instances / number of classes

  Per-class structural metrics (OQuaRE, adapted from Chidamber & Kemerer OO metrics):
    DIT - Depth of Inheritance Tree
    NOC - Number Of Children (direct subclasses)
    NAC - Number of Ancestor Classes
    CBO - Coupling Between Object classes (classes reachable via a shared property)
    WMC - Weighted Methods per Class (properties declared directly on the class)
    RFC - Response For a Class (WMC plus properties inherited from ancestors)
    TM  - Tangled (the class has more than one direct parent)

  Functional adequacy / documentation:
    how much of the schema is explicitly declared (owl:Class, rdfs:domain,
    rdfs:range) vs. merely implied by usage, and rdfs:label/rdfs:comment
    coverage on classes and properties.

Unlike graph_quality.py, Class Richness here is *not* trivially 1.0: a class
declared with owl:Class/rdfs:Class but never used in any rdf:type triple
counts against it, which is exactly the "unpopulated class" smell OntoQA's
CR metric is meant to catch.

As with graph_quality.py, prefix/namespace-legend triples added by
tarql_visualiser.py are excluded before any of this is computed.
"""

import argparse
import json
import sys
from collections import defaultdict

from rdflib import RDF, RDFS, OWL
from rdflib.term import BNode, Literal, URIRef

from .. import hierarchy
from .graph_quality import default_ignored_predicates, load_data_graph
from .tarql_visualiser import (
    DEFAULT_BASE,
    DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    DEFAULT_NAMESPACE_PREDICATE,
)

ANNOTATION_PREDICATES = {RDFS.label, RDFS.comment}
UNCLASSIFIED = "(untyped)"


def _is_class_node(term):
    return isinstance(term, (URIRef, BNode))


def _real_classes(nodes):
    return {n for n in nodes if _is_class_node(n)}


def induce_schema(graph):
    """Derive an implicit ontology schema from how the data graph uses classes and properties."""
    explicit_classes = {s for s in graph.subjects(RDF.type, OWL.Class)} | {
        s for s in graph.subjects(RDF.type, RDFS.Class)
    }

    type_triples = [
        (s, o) for s, p, o in graph if p == RDF.type and o not in (OWL.Class, RDFS.Class)
    ]
    used_classes = {o for s, o in type_triples}

    entity_types = defaultdict(set)
    instances_by_class = defaultdict(set)
    for s, o in type_triples:
        entity_types[s].add(o)
        instances_by_class[o].add(s)

    parents_of = defaultdict(set)
    children_of = defaultdict(set)
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        parents_of[child].add(parent)
        children_of[parent].add(child)

    classes = explicit_classes | used_classes | set(parents_of) | set(children_of)

    explicit_domain = defaultdict(set)
    for prop, cls in graph.subject_objects(RDFS.domain):
        explicit_domain[prop].add(cls)
    explicit_range = defaultdict(set)
    for prop, cls in graph.subject_objects(RDFS.range):
        explicit_range[prop].add(cls)

    non_type_triples = [(s, p, o) for s, p, o in graph if p != RDF.type and p not in ANNOTATION_PREDICATES]
    properties = {p for s, p, o in non_type_triples}

    inferred_domain = defaultdict(set)
    inferred_range = defaultdict(set)
    property_has_literal = defaultdict(bool)
    property_has_resource = defaultdict(bool)
    for s, p, o in non_type_triples:
        subj_types = entity_types.get(s)
        inferred_domain[p].update(subj_types if subj_types else {UNCLASSIFIED})
        if isinstance(o, Literal):
            property_has_literal[p] = True
        else:
            property_has_resource[p] = True
            obj_types = entity_types.get(o)
            inferred_range[p].update(obj_types if obj_types else {UNCLASSIFIED})

    domain = {p: (explicit_domain[p] or inferred_domain.get(p, set())) for p in properties}
    range_ = {p: (explicit_range[p] or inferred_range.get(p, set())) for p in properties}

    annotated = {s for s in graph.subjects(RDFS.label, None)} | {s for s in graph.subjects(RDFS.comment, None)}

    return {
        "classes": classes,
        "explicit_classes": explicit_classes,
        "entity_types": entity_types,
        "instances_by_class": instances_by_class,
        "parents_of": parents_of,
        "children_of": children_of,
        "properties": properties,
        "domain": domain,
        "range": range_,
        "explicit_domain": explicit_domain,
        "explicit_range": explicit_range,
        "property_has_literal": property_has_literal,
        "property_has_resource": property_has_resource,
        "annotated": annotated,
    }


# Shared with ontologyeval/sketch/dataquality -- see
# ``ontology_suite/hierarchy.py`` for why these are iterative rather than
# recursive (a deep enough subclass chain overflowed CPython's stack from
# what is only a metrics report).
_ancestors = hierarchy.ancestors
_depth = hierarchy.depth


def compute_metrics(schema):
    classes = schema["classes"]
    properties = schema["properties"]
    domain, range_ = schema["domain"], schema["range"]
    parents_of, children_of = schema["parents_of"], schema["children_of"]

    dit_memo, ancestor_memo = {}, {}
    per_class = {}
    for cls in classes:
        ancestors = _ancestors(cls, parents_of, ancestor_memo)
        own_props = {p for p in properties if cls in domain[p]}
        inherited_props = {p for p in properties if domain[p] & ancestors}
        coupled = set()
        for p in properties:
            if cls in domain[p]:
                coupled |= _real_classes(range_[p]) - {cls}
            if cls in range_[p]:
                coupled |= _real_classes(domain[p]) - {cls}
        per_class[cls] = {
            "instances": len(schema["instances_by_class"].get(cls, set())),
            "dit": _depth(cls, parents_of, dit_memo),
            "noc": len(children_of.get(cls, set())),
            "nac": len(ancestors),
            "cbo": len(coupled),
            "wmc": len(own_props),
            "rfc": len(own_props | inherited_props),
            "tangled": len(parents_of.get(cls, set())) > 1,
            "annotated": cls in schema["annotated"],
        }

    datatype_properties = {
        p for p in properties if schema["property_has_literal"][p] and not schema["property_has_resource"][p]
    }
    object_properties = {
        p for p in properties if schema["property_has_resource"][p] and not schema["property_has_literal"][p]
    }
    mixed_properties = {
        p for p in properties if schema["property_has_literal"][p] and schema["property_has_resource"][p]
    }

    subclass_edge_count = sum(len(parents) for parents in parents_of.values())
    attribute_slots = sum(len(_real_classes(domain[p]) or {UNCLASSIFIED}) for p in datatype_properties)
    classes_with_instances = {c for c in classes if schema["instances_by_class"].get(c)}
    typed_instance_count = len(schema["entity_types"])

    richness = {
        "attribute_richness": round(attribute_slots / len(classes), 3) if classes else 0.0,
        "relationship_richness": round(len(object_properties) / (len(object_properties) + subclass_edge_count), 3)
        if (object_properties or subclass_edge_count)
        else 0.0,
        "inheritance_richness": round(subclass_edge_count / len(classes), 3) if classes else 0.0,
        "class_richness": round(len(classes_with_instances) / len(classes), 3) if classes else 0.0,
        "average_population": round(typed_instance_count / len(classes), 3) if classes else 0.0,
    }

    properties_no_explicit_domain = {p for p in properties if not schema["explicit_domain"].get(p)}
    properties_no_explicit_range = {p for p in properties if not schema["explicit_range"].get(p)}
    properties_untyped_domain = {p for p in properties if UNCLASSIFIED in domain[p]}
    properties_untyped_range = {p for p in properties if UNCLASSIFIED in range_[p]}
    annotated_properties = {p for p in properties if p in schema["annotated"]}

    completeness = {
        "class_count": len(classes),
        "explicit_class_count": len(schema["explicit_classes"]),
        "induced_class_count": len(classes) - len(schema["explicit_classes"]),
        "property_count": len(properties),
        "explicit_domain_count": len(properties) - len(properties_no_explicit_domain),
        "explicit_range_count": len(properties) - len(properties_no_explicit_range),
        "annotated_class_count": sum(1 for c in per_class.values() if c["annotated"]),
        "annotated_property_count": len(annotated_properties),
    }

    return {
        "richness": richness,
        "completeness": completeness,
        "per_class": per_class,
        "properties": {
            "datatype_properties": datatype_properties,
            "object_properties": object_properties,
            "mixed_properties": mixed_properties,
            "properties_untyped_domain": properties_untyped_domain,
            "properties_untyped_range": properties_untyped_range,
        },
        "flags": {
            "induced_classes": sorted(schema["classes"] - schema["explicit_classes"], key=str),
            "tangled_classes": sorted((c for c, m in per_class.items() if m["tangled"]), key=str),
            "mixed_properties": sorted(mixed_properties, key=str),
            "properties_untyped_domain": sorted(properties_untyped_domain, key=str),
            "properties_untyped_range": sorted(properties_untyped_range, key=str),
        },
    }


def _label(term, graph):
    if term == UNCLASSIFIED:
        return UNCLASSIFIED
    if isinstance(term, Literal):
        return f'"{term}"'
    return graph.qname(term)


def print_report(path, ignored_count, metrics, graph, top=20):
    richness, completeness = metrics["richness"], metrics["completeness"]

    print(f"=== Ontology quality report: {path} ===")
    print(f"(ignored {ignored_count} prefix/namespace-legend triple(s))")
    print()

    print("-- Schema completeness (functional adequacy) --")
    print(
        f"Classes:    {completeness['class_count']} "
        f"({completeness['explicit_class_count']} explicitly declared, "
        f"{completeness['induced_class_count']} only induced from usage)"
    )
    print(
        f"Properties: {completeness['property_count']} "
        f"({completeness['explicit_domain_count']} with explicit rdfs:domain, "
        f"{completeness['explicit_range_count']} with explicit rdfs:range)"
    )
    print(
        f"Annotated (rdfs:label/rdfs:comment): "
        f"{completeness['annotated_class_count']}/{completeness['class_count']} classes, "
        f"{completeness['annotated_property_count']}/{completeness['property_count']} properties"
    )
    print()

    print("-- Schema richness (OntoQA) --")
    print(f"Attribute Richness (AR):     {richness['attribute_richness']:<6}  attribute slots per class")
    print(f"Relationship Richness (RR):  {richness['relationship_richness']:<6}  object properties vs. object properties + subclass edges")
    print(f"Inheritance Richness (IR):   {richness['inheritance_richness']:<6}  avg subclasses per class")
    print()

    print("-- Instance richness (OntoQA) --")
    print(f"Class Richness (CR):         {richness['class_richness']:<6}  classes with >=1 instance")
    print(f"Average Population (AP):     {richness['average_population']:<6}  typed instances per class")
    print()

    print("-- Per-class structural metrics (OQuaRE) --")
    rows = sorted(metrics["per_class"].items(), key=lambda kv: kv[1]["rfc"], reverse=True)
    if top > 0:
        rows = rows[:top]
    if rows:
        print(f"{'class':<28}{'inst':>6}{'dit':>5}{'noc':>5}{'nac':>5}{'cbo':>5}{'wmc':>5}{'rfc':>5}  tangled")
        for cls, m in rows:
            print(
                f"{_label(cls, graph):<28}{m['instances']:>6}{m['dit']:>5}{m['noc']:>5}{m['nac']:>5}"
                f"{m['cbo']:>5}{m['wmc']:>5}{m['rfc']:>5}  {'yes' if m['tangled'] else ''}"
            )
        omitted = len(metrics["per_class"]) - len(rows)
        if omitted > 0:
            print(f"... (+{omitted} more, use --top 0 to show all)")
    else:
        print("(no classes)")
    print()

    print("-- Quality flags --")
    flags_raised = False
    flags = metrics["flags"]
    if flags["induced_classes"]:
        flags_raised = True
        names = ", ".join(_label(c, graph) for c in flags["induced_classes"][:10])
        more = len(flags["induced_classes"]) - 10
        print(f"[!] {len(flags['induced_classes'])} class(es) only inferred from usage, never declared with owl:Class/rdfs:Class: {names}" + (f", ... (+{more} more)" if more > 0 else ""))
    if flags["tangled_classes"]:
        flags_raised = True
        names = ", ".join(_label(c, graph) for c in flags["tangled_classes"])
        print(f"[!] {len(flags['tangled_classes'])} class(es) have more than one direct parent: {names}")
    if flags["mixed_properties"]:
        flags_raised = True
        names = ", ".join(_label(p, graph) for p in flags["mixed_properties"])
        print(f"[!] {len(flags['mixed_properties'])} propert(y/ies) used with inconsistent ranges (sometimes a literal, sometimes a resource): {names}")
    if flags["properties_untyped_domain"]:
        flags_raised = True
        names = ", ".join(_label(p, graph) for p in flags["properties_untyped_domain"])
        print(f"[!] {len(flags['properties_untyped_domain'])} propert(y/ies) used on untyped subjects, so their domain could not be inferred: {names}")
    if flags["properties_untyped_range"]:
        flags_raised = True
        names = ", ".join(_label(p, graph) for p in flags["properties_untyped_range"])
        print(f"[!] {len(flags['properties_untyped_range'])} propert(y/ies) used with untyped resource objects, so their range could not be inferred: {names}")
    if not flags_raised:
        print("(none)")


def main(argv):
    parser = argparse.ArgumentParser(
        prog="ontology-quality",
        description="Computes OQuaRE/OntoQA schema-level quality metrics for a consolidated "
        "turtle file produced by tarql_visualiser.py, ignoring its prefix/namespace-legend triples.",
        epilog="version 0.1",
    )
    parser.add_argument("input", help="the turtle file to analyse")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"base IRI the input was written with (default: {DEFAULT_BASE})")
    parser.add_argument(
        "--namespace-predicate",
        default=DEFAULT_NAMESPACE_PREDICATE,
        help=f"predicate to ignore for the namespace legend (default: {DEFAULT_NAMESPACE_PREDICATE})",
    )
    parser.add_argument(
        "--namespace-conflict-predicate",
        default=DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
        help=f"predicate to ignore for the prefix-conflict flag (default: {DEFAULT_NAMESPACE_CONFLICT_PREDICATE})",
    )
    parser.add_argument("--top", type=int, default=20, help="max rows in the per-class table, 0 for all (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text report")
    args = parser.parse_args(argv)

    ignored_predicates = default_ignored_predicates(args.base, args.namespace_predicate, args.namespace_conflict_predicate)
    graph, ignored_count = load_data_graph(args.input, ignored_predicates)
    schema = induce_schema(graph)
    metrics = compute_metrics(schema)

    if args.json:
        json_metrics = {
            "richness": metrics["richness"],
            "completeness": metrics["completeness"],
            "per_class": {graph.qname(c): m for c, m in metrics["per_class"].items()},
            "properties": {k: [graph.qname(p) for p in v] for k, v in metrics["properties"].items()},
            "flags": {k: [_label(x, graph) for x in v] for k, v in metrics["flags"].items()},
            "ignored_triple_count": ignored_count,
        }
        print(json.dumps(json_metrics, indent=2))
    else:
        print_report(args.input, ignored_count, metrics, graph, top=args.top)


def run_tool():
    main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"])


if __name__ == "__main__":
    run_tool()
