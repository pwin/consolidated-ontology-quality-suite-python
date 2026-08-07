"""Compute graph-quality statistics for a Turtle file produced by tarql_visualiser.py.

The metrics are adapted from ontology-quality frameworks such as OQuaRE
(Duque-Ramos et al.) and OntoQA (Tartir et al.) - attribute/relationship/class
richness, tangledness, connectivity - but reinterpreted for a plain RDF
instance graph rather than a formal OWL ontology, since tarql CONSTRUCT
templates sketch entities and relationships with no explicit class/property
declarations:

  - "classes"  = the distinct values used as objects of rdf:type
  - "entities" = the distinct resources that participate as a subject, or as
                 the (non-literal) object of a non-typing triple
  - "properties" = the distinct predicates other than rdf:type

The namespace-legend triples tarql_visualiser.py adds (`:isRepresentedBy` /
`:hasAmbiguousPrefix`) describe the *queries*, not the *data*, so they are
excluded from the graph analysed here.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

import rdflib
from rdflib import RDF
from rdflib.term import Literal, URIRef

from .tarql_visualiser import (
    DEFAULT_BASE,
    DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    DEFAULT_NAMESPACE_PREDICATE,
    scratch_namespace,
)


def local_name(uri):
    """The fragment/last-path-segment of a URI, e.g. 'Person' from both
    http://xmlns.com/foaf/0.1/Person and http://schema.org/Person - used to
    spot the same-named type or property modelled under different namespaces.
    """
    text = str(uri)
    for sep in ("#", "/"):
        if sep in text:
            tail = text.rsplit(sep, 1)[1]
            if tail:
                return tail
    return text


def cross_namespace_groups(terms):
    """Group `terms` (class or property IRIs) by local name, keeping only groups
    of 2+ distinct IRIs - i.e. the same-named term defined under different namespaces.
    """
    by_name = defaultdict(set)
    for term in terms:
        by_name[local_name(term)].add(term)
    return {name: sorted(iris, key=str) for name, iris in by_name.items() if len(iris) > 1}


def _resolve_predicate(predicate, scratch_namespace):
    """Turn a `:localName` or full IRI into the full IRI to match against parsed triples."""
    if predicate.startswith(":"):
        return scratch_namespace + predicate[1:]
    return predicate


def default_ignored_predicates(
    base=DEFAULT_BASE,
    namespace_predicate=DEFAULT_NAMESPACE_PREDICATE,
    namespace_conflict_predicate=DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
):
    """The prefix-legend predicates tarql_visualiser.py adds by default, as full IRIs.

    ``tarql_visualiser.write_turtle`` always emits ``@base <base> .`` followed
    by ``@prefix : <scratch_namespace(base)> .`` (unless the query itself
    defines an empty prefix) -- see that function's own docstring for
    exactly how the scratch namespace is derived from ``base``. Reusing it
    here (rather than re-deriving it) is what keeps this ignore-set able to
    actually match the legend triples ``write_turtle`` produced.
    """
    ns = scratch_namespace(base)
    return {
        _resolve_predicate(namespace_predicate, ns),
        _resolve_predicate(namespace_conflict_predicate, ns),
    }


def load_data_graph(path, ignored_predicates=None):
    """Parse a turtle file and return (data_graph, ignored_triple_count).

    `data_graph` excludes any triple whose predicate is in `ignored_predicates`
    (the prefix/namespace-legend metadata triples, by default).
    """
    # bind_namespaces="none": rdflib's default namespace set binds e.g. "schema" to
    # https://schema.org/, which would collide with (and rename) a file's own
    # `@prefix schema: <http://schema.org/>` - keep only what the file declares.
    ignored = {URIRef(p) for p in (ignored_predicates or default_ignored_predicates())}
    full_graph = rdflib.Graph(bind_namespaces="none")
    full_graph.parse(path, format="turtle")

    data_graph = rdflib.Graph(bind_namespaces="none")
    for prefix, namespace in full_graph.namespaces():
        data_graph.bind(prefix, namespace)

    ignored_count = 0
    for s, p, o in full_graph:
        if p in ignored:
            ignored_count += 1
        else:
            data_graph.add((s, p, o))
    return data_graph, ignored_count


def _connected_components(nodes, edges):
    """Union-find over `nodes` connected by `edges` (iterable of (a, b) pairs)."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    components = defaultdict(set)
    for n in nodes:
        components[find(n)].add(n)
    return list(components.values())


def compute_metrics(graph):
    """Compute OQuaRE/OntoQA-inspired quality metrics for a (prefix-triple-free) rdflib.Graph."""
    triples = list(graph)

    type_triples = [(s, o) for s, p, o in triples if p == RDF.type]
    non_type_triples = [(s, p, o) for s, p, o in triples if p != RDF.type]
    literal_triples = [(s, p, o) for s, p, o in non_type_triples if isinstance(o, Literal)]
    object_triples = [(s, p, o) for s, p, o in non_type_triples if not isinstance(o, Literal)]

    entities = set()
    for s, p, o in triples:
        entities.add(s)
        if p != RDF.type and not isinstance(o, Literal):
            entities.add(o)

    classes = {o for s, o in type_triples}
    properties = {p for s, p, o in non_type_triples}

    types_by_entity = defaultdict(set)
    for s, o in type_triples:
        types_by_entity[s].add(o)
    typed_entities = {e for e in types_by_entity if e in entities}
    untyped_entities = entities - typed_entities
    multi_typed_entities = {e for e in typed_entities if len(types_by_entity[e]) > 1}

    classes_with_instances = {o for s, o in type_triples if s in entities}

    out_degree = Counter(s for s, p, o in triples if p != RDF.type)
    in_degree = Counter(o for s, p, o in object_triples)
    degrees = {e: out_degree.get(e, 0) + in_degree.get(e, 0) for e in entities}

    components = _connected_components(entities, [(s, o) for s, p, o in object_triples])

    predicate_uses = Counter(p for s, p, o in non_type_triples)
    predicate_subjects = defaultdict(set)
    predicate_objects = defaultdict(set)
    for s, p, o in non_type_triples:
        predicate_subjects[p].add(s)
        predicate_objects[p].add(o)

    predicate_table = []
    mixed_range_predicates = []
    for p in properties:
        objs = predicate_objects[p]
        has_literal = any(isinstance(o, Literal) for o in objs)
        has_resource = any(not isinstance(o, Literal) for o in objs)
        range_kind = "mixed" if has_literal and has_resource else ("literal" if has_literal else "resource")
        if range_kind == "mixed":
            mixed_range_predicates.append(p)
        predicate_table.append(
            {
                "predicate": p,
                "uses": predicate_uses[p],
                "subjects": len(predicate_subjects[p]),
                "objects": len(predicate_objects[p]),
                "range": range_kind,
            }
        )
    predicate_table.sort(key=lambda row: row["uses"], reverse=True)

    cross_namespace_types = cross_namespace_groups(classes)
    cross_namespace_properties = cross_namespace_groups(properties)

    return {
        "sizes": {
            "triple_count": len(triples),
            "entity_count": len(entities),
            "class_count": len(classes),
            "property_count": len(properties),
            "type_triple_count": len(type_triples),
            "literal_triple_count": len(literal_triples),
            "object_triple_count": len(object_triples),
        },
        "richness": {
            "attribute_richness": round(len(literal_triples) / len(entities), 3) if entities else 0.0,
            "relationship_richness": round(len(object_triples) / (len(object_triples) + len(type_triples)), 3)
            if (object_triples or type_triples)
            else 0.0,
            "class_richness": round(len(classes_with_instances) / len(classes), 3) if classes else 0.0,
            "average_population": round(len(typed_entities) / len(classes), 3) if classes else 0.0,
        },
        "typing": {
            "untyped_entity_count": len(untyped_entities),
            "untyped_entity_ratio": round(len(untyped_entities) / len(entities), 3) if entities else 0.0,
            "multi_typed_entity_count": len(multi_typed_entities),
        },
        "connectivity": {
            "connected_component_count": len(components),
            "largest_component_size": max((len(c) for c in components), default=0),
            "isolated_entity_count": sum(1 for c in components if len(c) == 1),
            "average_degree": round(sum(degrees.values()) / len(entities), 3) if entities else 0.0,
            "max_degree": max(degrees.values(), default=0),
        },
        "predicate_table": predicate_table,
        "cross_namespace": {
            "types": cross_namespace_types,
            "properties": cross_namespace_properties,
        },
        "flags": {
            "mixed_range_predicates": mixed_range_predicates,
            "untyped_entities": sorted(untyped_entities, key=str),
            "multi_typed_entities": sorted(multi_typed_entities, key=str),
        },
        "_degrees": degrees,  # kept for report formatting (max-degree entity); not emitted in --json
    }


def _label(term, graph):
    if isinstance(term, Literal):
        return f'"{term}"'
    return graph.qname(term)


def print_report(path, ignored_count, metrics, graph, top=20):
    sizes, richness, typing, connectivity = (
        metrics["sizes"],
        metrics["richness"],
        metrics["typing"],
        metrics["connectivity"],
    )

    print(f"=== Graph quality report: {path} ===")
    print(f"(ignored {ignored_count} prefix/namespace-legend triple(s))")
    print()

    print("-- Size --")
    print(f"Triples:    {sizes['triple_count']}")
    print(f"Entities:   {sizes['entity_count']}")
    print(f"Classes:    {sizes['class_count']}")
    print(f"Properties: {sizes['property_count']}")
    print()

    print("-- Richness (OQuaRE/OntoQA-inspired) --")
    print(f"Attribute richness (AR):      {richness['attribute_richness']:<6}  avg literal-valued triples per entity")
    print(
        f"Relationship richness (RR):   {richness['relationship_richness']:<6}  "
        f"share of object-to-object triples among all structural (object + rdf:type) triples"
    )
    print(
        f"Class richness (CR):          {richness['class_richness']:<6}  "
        f"fraction of classes with >=1 instance (always 1.0 here - a class only appears because something used it)"
    )
    print(f"Average population:           {richness['average_population']:<6}  typed entities per class")
    print()

    print("-- Typing quality --")
    print(
        f"Untyped entities: {typing['untyped_entity_count']} "
        f"({typing['untyped_entity_ratio'] * 100:.1f}%)"
    )
    if metrics["flags"]["untyped_entities"]:
        sample = ", ".join(_label(e, graph) for e in metrics["flags"]["untyped_entities"][:10])
        more = len(metrics["flags"]["untyped_entities"]) - 10
        print(f"  e.g. {sample}" + (f", ... (+{more} more)" if more > 0 else ""))
    print(f"Multi-typed entities (tangledness): {typing['multi_typed_entity_count']}")
    if metrics["flags"]["multi_typed_entities"]:
        sample = ", ".join(_label(e, graph) for e in metrics["flags"]["multi_typed_entities"][:10])
        more = len(metrics["flags"]["multi_typed_entities"]) - 10
        print(f"  e.g. {sample}" + (f", ... (+{more} more)" if more > 0 else ""))
    print()

    print("-- Connectivity --")
    print(f"Connected components:  {connectivity['connected_component_count']}")
    print(f"Largest component:     {connectivity['largest_component_size']} entities")
    print(f"Isolated entities:     {connectivity['isolated_entity_count']} (no relationships to other entities)")
    print(f"Average degree:        {connectivity['average_degree']}")
    degrees = metrics["_degrees"]
    if degrees:
        busiest = max(degrees, key=degrees.get)
        print(f"Max degree:            {connectivity['max_degree']}  ({_label(busiest, graph)})")
    print()

    print("-- Predicate usage --")
    rows = metrics["predicate_table"] if top <= 0 else metrics["predicate_table"][:top]
    if rows:
        print(f"{'predicate':<30}{'uses':>6}{'subjects':>10}{'objects':>9}  range")
        for row in rows:
            print(
                f"{_label(row['predicate'], graph):<30}{row['uses']:>6}{row['subjects']:>10}"
                f"{row['objects']:>9}  {row['range']}"
            )
        omitted = len(metrics["predicate_table"]) - len(rows)
        if omitted > 0:
            print(f"... (+{omitted} more, use --top 0 to show all)")
    else:
        print("(no properties)")
    print()

    print("-- Cross-namespace overlap --")
    cross_ns = metrics["cross_namespace"]
    if cross_ns["types"] or cross_ns["properties"]:
        for name, iris in cross_ns["types"].items():
            print(f"type '{name}' defined in {len(iris)} namespaces: {', '.join(graph.qname(i) for i in iris)}")
        for name, iris in cross_ns["properties"].items():
            print(f"property '{name}' defined in {len(iris)} namespaces: {', '.join(graph.qname(i) for i in iris)}")
    else:
        print("(none - no type or property local name is reused across namespaces)")
    print()

    print("-- Quality flags --")
    flags_raised = False
    if metrics["cross_namespace"]["types"]:
        flags_raised = True
        print(
            f"[!] {len(metrics['cross_namespace']['types'])} type name(s) appear under more than one namespace "
            f"- possible vocabulary duplication, e.g. one file using foaf:Person and another schema:Person for the same concept"
        )
    if metrics["cross_namespace"]["properties"]:
        flags_raised = True
        print(f"[!] {len(metrics['cross_namespace']['properties'])} property name(s) appear under more than one namespace")
    if metrics["flags"]["mixed_range_predicates"]:
        flags_raised = True
        names = ", ".join(_label(p, graph) for p in metrics["flags"]["mixed_range_predicates"])
        print(f"[!] predicate(s) used with inconsistent ranges (sometimes a literal, sometimes a resource): {names}")
    if typing["untyped_entity_count"]:
        flags_raised = True
        print(f"[!] {typing['untyped_entity_count']} entities have no rdf:type")
    if connectivity["isolated_entity_count"]:
        flags_raised = True
        print(f"[!] {connectivity['isolated_entity_count']} entities are isolated (only ever typed or attributed, never related to another entity)")
    if not flags_raised:
        print("(none)")


def main(argv):
    parser = argparse.ArgumentParser(
        prog="graph-quality",
        description="Computes OQuaRE/OntoQA-inspired quality statistics for a consolidated "
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
    parser.add_argument("--top", type=int, default=20, help="max rows in the predicate-usage table, 0 for all (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text report")
    args = parser.parse_args(argv)

    ignored_predicates = default_ignored_predicates(args.base, args.namespace_predicate, args.namespace_conflict_predicate)
    graph, ignored_count = load_data_graph(args.input, ignored_predicates)
    metrics = compute_metrics(graph)

    if args.json:
        json_metrics = {k: v for k, v in metrics.items() if k != "_degrees"}
        json_metrics["predicate_table"] = [
            {**row, "predicate": str(row["predicate"])} for row in json_metrics["predicate_table"]
        ]
        for key in ("mixed_range_predicates",):
            json_metrics["flags"][key] = [str(p) for p in json_metrics["flags"][key]]
        for key in ("untyped_entities", "multi_typed_entities"):
            json_metrics["flags"][key] = [str(e) for e in json_metrics["flags"][key]]
        json_metrics["cross_namespace"] = {
            group: {name: [str(iri) for iri in iris] for name, iris in groups.items()}
            for group, groups in json_metrics["cross_namespace"].items()
        }
        json_metrics["ignored_triple_count"] = ignored_count
        print(json.dumps(json_metrics, indent=2))
    else:
        print_report(args.input, ignored_count, metrics, graph, top=args.top)


def run_tool():
    main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"])


if __name__ == "__main__":
    run_tool()
