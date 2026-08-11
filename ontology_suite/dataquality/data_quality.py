"""Assess a collection of RDF data-graph files (Turtle), inspired by OQuaRE
and OntoQA, optionally checked against a real OWL2/RDFS ontology file that
supplied the classes and relationships used to build them.

This differs from graph_quality.py and ontology_quality.py in three ways:

  1. It takes MANY data files (or folders of them) rather than one, merges
     them into a single aggregate graph, and also reports a per-file
     breakdown - useful when data arrives as separate batches/exports.

  2. An ontology file is optional but, when given, is treated as the ground
     truth schema instead of one induced from usage. That makes richness
     metrics like Class Richness genuinely meaningful (a declared-but-never-
     populated class now actually lowers it) rather than trivially 1.0.

  3. With an ontology, this script also checks CONFORMANCE: classes/
     properties the data uses but the ontology never declared, classes the
     ontology declares but the data never populates, and rdfs:domain/
     rdfs:range violations (a property used on a subject/object whose type
     isn't covered by what the ontology says that property's domain/range
     should be).

Scope/limitations, stated up front rather than silently:
  - Only direct `rdfs:domain` / `rdfs:range` / `rdfs:subClassOf` triples are
    consulted. OWL2 restriction-based constraints (owl:someValuesFrom etc.)
    are not evaluated.
  - RDFS formally treats multiple `rdfs:domain`/`rdfs:range` triples on one
    property as a CONJUNCTION (the subject must satisfy every one). This
    script instead treats them as alternatives (at least one must be
    satisfied), since that's usually what's intended in practice and is a
    more useful signal for a quality report than strict RDFS entailment.

As with the other two scripts, tarql_visualiser.py's namespace-legend
triples (`:isRepresentedBy` / `:hasAmbiguousPrefix`) are excluded from every
data file before anything is computed.
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import rdflib
from rdflib import OWL, RDF, RDFS, XSD
from rdflib.namespace import SKOS
from rdflib.term import Literal

from ..sketch.graph_quality import (
    compute_metrics as compute_data_metrics,
    cross_namespace_groups,
    default_ignored_predicates,
    load_data_graph,
    print_report as print_data_report,
)
from ..sketch.tarql_visualiser import (
    DEFAULT_BASE,
    DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    DEFAULT_NAMESPACE_PREDICATE,
)
from ..checks.merge import ResultRow

DEFAULT_DATA_GLOBS = "*.ttl,*.turtle"
# SKOS's own lexical-label/documentation predicates -- never meant to be
# locally re-declared as an owl:AnnotationProperty by every ontology that
# uses them, same as rdfs:label/rdfs:comment. Missing skos:prefLabel here
# specifically was caught as a real bug: any SKOS-labeled taxonomy (gist's
# own gist:Category individuals included) checked with check_conformance()
# flooded a false "undeclared property" (CNF-002) for every single
# skos:prefLabel triple, purely because this set never learned about SKOS
# -- the same class of false positive QUA-001/QUA-002/STR-003 were each
# separately caught and fixed for elsewhere in this suite's checks/ registry.
ANNOTATION_PREDICATES = {
    RDFS.label, RDFS.comment,
    SKOS.prefLabel, SKOS.altLabel, SKOS.hiddenLabel,
    SKOS.definition, SKOS.note, SKOS.scopeNote, SKOS.example,
    SKOS.historyNote, SKOS.editorialNote, SKOS.changeNote,
}
SCHEMA_PREDICATES = {RDFS.subClassOf, RDFS.domain, RDFS.range}

# The built-in RDF/RDFS/OWL2 vocabulary: legitimately used as an rdf:type
# value (most commonly on blank-node axioms -- every owl:Restriction-typed
# subclass-axiom blank node, every owl:AllDisjointClasses collection, etc.)
# without a graph ever declaring e.g. "owl:Restriction a owl:Class" itself --
# that's assumed axiomatically from the OWL2 spec, not something any real
# ontology re-asserts. Missing owl:Restriction here specifically was caught
# as a real bug: it flooded 149 false "undefined class" findings against a
# vehicle ontology importing gist 14.1.0, one per anonymous restriction.
META_TYPES = {
    OWL.Thing, OWL.Nothing, RDFS.Resource,
    OWL.Class, RDFS.Class, OWL.Restriction, RDFS.Datatype,
    OWL.NamedIndividual, OWL.Ontology,
    OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property,
    OWL.FunctionalProperty, OWL.InverseFunctionalProperty, OWL.TransitiveProperty,
    OWL.SymmetricProperty, OWL.AsymmetricProperty, OWL.ReflexiveProperty, OWL.IrreflexiveProperty,
    OWL.AllDisjointClasses, OWL.AllDisjointProperties, OWL.AllDifferent,
    OWL.NegativePropertyAssertion, OWL.Axiom,
}


def resolve_input_paths(inputs, patterns=DEFAULT_DATA_GLOBS):
    """Expand a mix of file, folder, and http(s) URL inputs into a sorted
    list of turtle file paths/URLs. A folder is also searched for each
    pattern's gzip-compressed form (`*.ttl.gz` alongside `*.ttl`), so a
    folder of gzip-compressed data files is discovered too -- `load_data_graph`
    (via `io_utils`) decompresses them transparently once loaded."""
    pattern_list = [p.strip() for p in patterns.split(",")]
    pattern_list += [p + ".gz" for p in pattern_list]

    paths = set()
    for item in inputs:
        if os.path.isdir(item):
            for pattern in pattern_list:
                paths.update(glob.glob(os.path.join(item, pattern)))
        else:
            paths.add(item)
    if not paths:
        raise FileNotFoundError(f"No turtle files found among: {inputs}")
    return sorted(paths)


def load_data_graphs(paths, ignored_predicates=None):
    """Parse and merge several data files into one graph, keeping a per-file breakdown too."""
    combined = rdflib.Graph(bind_namespaces="none")
    per_file = []
    for path in paths:
        graph, ignored_count = load_data_graph(path, ignored_predicates)
        for prefix, namespace in graph.namespaces():
            combined.bind(prefix, namespace)
        for triple in graph:
            combined.add(triple)
        per_file.append({"path": path, "graph": graph, "ignored_triple_count": ignored_count})
    return combined, per_file


def summarize_file(graph):
    """A compact per-file quality snapshot (deliberately smaller than graph_quality's full report)."""
    triples = list(graph)
    type_triples = [(s, o) for s, p, o in triples if p == RDF.type]
    entities = set()
    for s, p, o in triples:
        entities.add(s)
        if p != RDF.type and not isinstance(o, Literal):
            entities.add(o)
    classes = {o for s, o in type_triples}
    properties = {p for s, p, o in triples if p != RDF.type}
    typed_entities = {s for s, o in type_triples}
    untyped = entities - typed_entities
    return {
        "triple_count": len(triples),
        "entity_count": len(entities),
        "class_count": len(classes),
        "property_count": len(properties),
        "untyped_entity_count": len(untyped),
        "untyped_entity_ratio": round(len(untyped) / len(entities), 3) if entities else 0.0,
    }


def load_ontology_graph(path):
    if path is None:
        return None
    graph = rdflib.Graph(bind_namespaces="none")
    graph.parse(path, format="turtle")
    return graph


def ontology_declarations(ontology_graph):
    """The ground-truth schema straight from the ontology file: no induction, no fallback."""
    declared_classes = set(ontology_graph.subjects(RDF.type, OWL.Class)) | set(
        ontology_graph.subjects(RDF.type, RDFS.Class)
    )
    declared_properties = set()
    for meta_type in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property):
        declared_properties |= set(ontology_graph.subjects(RDF.type, meta_type))

    domain = defaultdict(set)
    for prop, cls in ontology_graph.subject_objects(RDFS.domain):
        domain[prop].add(cls)
        declared_properties.add(prop)
    range_ = defaultdict(set)
    for prop, cls in ontology_graph.subject_objects(RDFS.range):
        range_[prop].add(cls)
        declared_properties.add(prop)

    parents_of = defaultdict(set)
    for child, parent in ontology_graph.subject_objects(RDFS.subClassOf):
        parents_of[child].add(parent)
        declared_classes.add(child)
        declared_classes.add(parent)

    annotated = set(ontology_graph.subjects(RDFS.label, None)) | set(ontology_graph.subjects(RDFS.comment, None))

    return {
        "classes": declared_classes,
        "properties": declared_properties,
        "domain": domain,
        "range": range_,
        "parents_of": parents_of,
        "annotated": annotated,
    }


def _ancestors(cls, parents_of, memo, visiting=frozenset()):
    if cls in memo:
        return memo[cls]
    if cls in visiting:
        return set()
    result = set()
    for parent in parents_of.get(cls, ()):
        result.add(parent)
        result |= _ancestors(parent, parents_of, memo, visiting | {cls})
    memo[cls] = result
    return result


def schema_richness(declarations, data_graph):
    """OntoQA-style richness computed straight from the ontology's own declarations,
    with population counts (instances, populated classes) coming from the data.
    """
    classes = declarations["classes"]
    domain, range_, parents_of = declarations["domain"], declarations["range"], declarations["parents_of"]

    object_properties, datatype_properties = set(), set()
    for p in declarations["properties"]:
        ranges = range_.get(p, set())
        is_datatype = any(str(c).startswith(str(XSD)) or c == RDFS.Literal for c in ranges)
        is_object = any(c in classes for c in ranges) or (not ranges and p not in range_)
        if is_datatype and not is_object:
            datatype_properties.add(p)
        elif is_object:
            object_properties.add(p)

    subclass_edge_count = sum(len(parents) for parents in parents_of.values())
    attribute_slots = sum(len(domain.get(p, set()) & classes) or 1 for p in datatype_properties)

    entity_types = defaultdict(set)
    for s, o in data_graph.subject_objects(RDF.type):
        entity_types[s].add(o)
    classes_with_instances = {c for c in classes if any(c in types for types in entity_types.values())}
    typed_instance_count = len(entity_types)

    return {
        "attribute_richness": round(attribute_slots / len(classes), 3) if classes else 0.0,
        "relationship_richness": round(len(object_properties) / (len(object_properties) + subclass_edge_count), 3)
        if (object_properties or subclass_edge_count)
        else 0.0,
        "inheritance_richness": round(subclass_edge_count / len(classes), 3) if classes else 0.0,
        "class_richness": round(len(classes_with_instances) / len(classes), 3) if classes else 0.0,
        "average_population": round(typed_instance_count / len(classes), 3) if classes else 0.0,
        "class_count": len(classes),
        "property_count": len(declarations["properties"]),
    }


def check_conformance(declarations, data_graph):
    """Compare a data graph against the ontology's explicit declarations.

    See the module docstring for the domain/range interpretation caveat.
    """
    entity_types = defaultdict(set)
    for s, o in data_graph.subject_objects(RDF.type):
        entity_types[s].add(o)
    ancestor_memo = {}

    def satisfies(term, declared_classes):
        types = entity_types.get(term)
        if not types:
            return None
        for t in types:
            covered = {t} | _ancestors(t, declarations["parents_of"], ancestor_memo)
            if covered & declared_classes:
                return True
        return False

    undeclared_classes_used = set()
    undeclared_properties_used = set()
    domain_violations = defaultdict(set)
    range_violations = defaultdict(set)
    unverifiable_domain = defaultdict(int)
    unverifiable_range = defaultdict(int)

    for s, p, o in data_graph:
        if p == RDF.type:
            if o not in declarations["classes"] and o not in META_TYPES:
                undeclared_classes_used.add(o)
            continue
        if p in ANNOTATION_PREDICATES or p in SCHEMA_PREDICATES:
            continue
        if p not in declarations["properties"]:
            undeclared_properties_used.add(p)
            continue

        domain_classes = declarations["domain"].get(p)
        if domain_classes:
            result = satisfies(s, domain_classes)
            if result is False:
                domain_violations[p].add(s)
            elif result is None:
                unverifiable_domain[p] += 1

        range_classes = declarations["range"].get(p)
        if range_classes:
            if isinstance(o, Literal):
                expected_datatypes = {c for c in range_classes if str(c).startswith(str(XSD)) or c == RDFS.Literal}
                if expected_datatypes:
                    actual = o.datatype or (RDFS.langString if o.language else XSD.string)
                    if actual not in expected_datatypes:
                        range_violations[p].add(o)
            else:
                result = satisfies(o, range_classes)
                if result is False:
                    range_violations[p].add(o)
                elif result is None:
                    unverifiable_range[p] += 1

    populated_classes = {c for types in entity_types.values() for c in types}
    unpopulated_classes = declarations["classes"] - populated_classes

    return {
        "undeclared_classes_used": undeclared_classes_used,
        "undeclared_properties_used": undeclared_properties_used,
        "unpopulated_classes": unpopulated_classes,
        "domain_violations": dict(domain_violations),
        "range_violations": dict(range_violations),
        "unverifiable_domain": dict(unverifiable_domain),
        "unverifiable_range": dict(unverifiable_range),
    }


def conformance_to_rows(conformance, source_label):
    """Convert ``check_conformance()``'s dict into the same ``ResultRow``
    shape every other check in this suite reports as, tagged with
    ``source_label`` (e.g. ``"data"`` or ``"sketch"``).

    This is what lets the pipeline's ``sketch`` stage reuse this exact
    conformance logic for the "does the TARQL/oxi-gen CONSTRUCT-query sketch
    actually match the ontology's declarations" diff, rather than
    reimplementing it: the sketch is just another data graph as far as
    ``check_conformance`` is concerned.
    """
    rows = []
    for cls in sorted(conformance["undeclared_classes_used"], key=str):
        rows.append(ResultRow(
            check_id="CNF-001", category="conformance",
            title="Class used but not declared in the ontology",
            severity="Warning", focus_node=str(cls), path=None, value=None,
            message=f"Class {cls} is used with rdf:type in the {source_label} graph but is never "
                    "declared owl:Class/rdfs:Class in the ontology.",
            remediation="Declare the class in the ontology, or fix the typo/undeclared-vocabulary use.",
            sources=[source_label],
        ))
    for prop in sorted(conformance["undeclared_properties_used"], key=str):
        rows.append(ResultRow(
            check_id="CNF-002", category="conformance",
            title="Property used but not declared in the ontology",
            severity="Warning", focus_node=str(prop), path=None, value=None,
            message=f"Property {prop} is used in the {source_label} graph but is never declared as a "
                    "property in the ontology.",
            remediation="Declare the property in the ontology, or fix the typo/undeclared-vocabulary use.",
            sources=[source_label],
        ))
    for prop, subjects in conformance["domain_violations"].items():
        for s in sorted(subjects, key=str):
            rows.append(ResultRow(
                check_id="CNF-003", category="conformance",
                title="rdfs:domain violation",
                severity="Violation", focus_node=str(s), path=str(prop), value=None,
                message=f"{s} uses property {prop} but its type doesn't match any of the property's "
                        f"declared rdfs:domain classes (in the {source_label} graph).",
                remediation="Fix the subject's type, or add/relax the property's rdfs:domain.",
                sources=[source_label],
            ))
    for prop, objects in conformance["range_violations"].items():
        for o in sorted(objects, key=str):
            rows.append(ResultRow(
                check_id="CNF-004", category="conformance",
                title="rdfs:range violation",
                severity="Violation", focus_node=str(o), path=str(prop), value=None,
                message=f"Value {o} of property {prop} doesn't match any of the property's declared "
                        f"rdfs:range classes/datatypes (in the {source_label} graph).",
                remediation="Fix the value's type/datatype, or add/relax the property's rdfs:range.",
                sources=[source_label],
            ))
    for cls in sorted(conformance["unpopulated_classes"], key=str):
        rows.append(ResultRow(
            check_id="CNF-005", category="conformance",
            title="Ontology class never populated",
            severity="Info", focus_node=str(cls), path=None, value=None,
            message=f"Class {cls} is declared in the ontology but the {source_label} graph never uses "
                    "it as an rdf:type.",
            remediation="Expected for classes outside this batch/sketch's scope; investigate only if "
                        "the class should always be populated.",
            sources=[source_label],
        ))
    return rows


def _label(term, graph):
    if isinstance(term, Literal):
        return f'"{term}"'
    return graph.qname(term)


def print_per_file_table(per_file):
    print("-- Per-file breakdown --")
    print(f"{'file':<30}{'triples':>9}{'entities':>10}{'classes':>9}{'props':>7}{'untyped':>9}{'ignored':>9}")
    for entry in per_file:
        summary = summarize_file(entry["graph"])
        print(
            f"{os.path.basename(entry['path']):<30}{summary['triple_count']:>9}{summary['entity_count']:>10}"
            f"{summary['class_count']:>9}{summary['property_count']:>7}{summary['untyped_entity_count']:>9}"
            f"{entry['ignored_triple_count']:>9}"
        )
    print()


def print_schema_richness(richness, ontology_path):
    print(f"-- Schema richness (OntoQA, ontology: {ontology_path}) --")
    print(f"Classes: {richness['class_count']}   Properties: {richness['property_count']}")
    print(f"Attribute Richness (AR):     {richness['attribute_richness']:<6}  attribute slots per declared class")
    print(f"Relationship Richness (RR):  {richness['relationship_richness']:<6}  object properties vs. object properties + subclass edges")
    print(f"Inheritance Richness (IR):   {richness['inheritance_richness']:<6}  avg subclasses per declared class")
    print(f"Class Richness (CR):         {richness['class_richness']:<6}  declared classes with >=1 instance in the data")
    print(f"Average Population (AP):     {richness['average_population']:<6}  typed instances per declared class")
    print()


def print_conformance(conformance, graph, top=20):
    print("-- Ontology conformance --")
    raised = False

    if conformance["undeclared_classes_used"]:
        raised = True
        names = sorted(_label(c, graph) for c in conformance["undeclared_classes_used"])
        print(f"[!] {len(names)} class(es) used in the data but not declared in the ontology: {', '.join(names[:top] if top else names)}")
    if conformance["undeclared_properties_used"]:
        raised = True
        names = sorted(_label(p, graph) for p in conformance["undeclared_properties_used"])
        print(f"[!] {len(names)} propert(y/ies) used in the data but not declared in the ontology: {', '.join(names[:top] if top else names)}")
    if conformance["unpopulated_classes"]:
        raised = True
        names = sorted(_label(c, graph) for c in conformance["unpopulated_classes"])
        print(f"[!] {len(names)} class(es) declared in the ontology but never populated by the data: {', '.join(names[:top] if top else names)}")
    if conformance["domain_violations"]:
        raised = True
        print("[!] rdfs:domain violations (subject's type doesn't match the declared domain):")
        for prop, subjects in conformance["domain_violations"].items():
            sample = ", ".join(_label(s, graph) for s in sorted(subjects, key=str)[:5])
            print(f"    {_label(prop, graph)}: {len(subjects)} offending subject(s), e.g. {sample}")
    if conformance["range_violations"]:
        raised = True
        print("[!] rdfs:range violations (object's type/datatype doesn't match the declared range):")
        for prop, objects in conformance["range_violations"].items():
            sample = ", ".join(_label(o, graph) for o in sorted(objects, key=str)[:5])
            print(f"    {_label(prop, graph)}: {len(objects)} offending object(s), e.g. {sample}")
    if conformance["unverifiable_domain"] or conformance["unverifiable_range"]:
        raised = True
        print("[!] domain/range could not be checked because the term was untyped:")
        for prop, count in conformance["unverifiable_domain"].items():
            print(f"    {_label(prop, graph)}: {count} untyped subject(s)")
        for prop, count in conformance["unverifiable_range"].items():
            print(f"    {_label(prop, graph)}: {count} untyped resource object(s)")
    if not raised:
        print("(none - the data conforms to everything the ontology declares)")
    print()


def main(argv):
    parser = argparse.ArgumentParser(
        prog="data-quality",
        description="Assesses one or more RDF data-graph turtle files (OQuaRE/OntoQA-inspired), "
        "optionally checked for conformance against a supplied OWL2/RDFS ontology file.",
        epilog="version 0.1",
    )
    parser.add_argument("inputs", nargs="+", help="one or more turtle data files, or folders of them")
    parser.add_argument(
        "--file-pattern",
        default=DEFAULT_DATA_GLOBS,
        help=f"comma-separated glob pattern(s) used to find files when an input is a folder (default: {DEFAULT_DATA_GLOBS})",
    )
    parser.add_argument("--ontology", default=None, help="an OWL2/RDFS ontology turtle file supplying the real classes/properties/hierarchy")
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
    parser.add_argument("--top", type=int, default=20, help="max rows/samples per table or flag, 0 for all (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text report")
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="print which files --file-pattern actually matched before running")
    args = parser.parse_args(argv)

    ignored_predicates = default_ignored_predicates(args.base, args.namespace_predicate, args.namespace_conflict_predicate)
    paths = resolve_input_paths(args.inputs, args.file_pattern)
    if args.verbose:
        print(f"[verbose] {args.inputs} (--file-pattern {args.file_pattern}): {len(paths)} file(s) matched:")
        for p in paths:
            print(f"    {p}")
    combined_graph, per_file = load_data_graphs(paths, ignored_predicates)
    total_ignored = sum(entry["ignored_triple_count"] for entry in per_file)
    data_metrics = compute_data_metrics(combined_graph)

    ontology_graph = load_ontology_graph(args.ontology)
    richness, conformance, declarations = None, None, None
    if ontology_graph is not None:
        declarations = ontology_declarations(ontology_graph)
        richness = schema_richness(declarations, combined_graph)
        conformance = check_conformance(declarations, combined_graph)

    if args.json:
        json_metrics = {k: v for k, v in data_metrics.items() if k != "_degrees"}
        json_metrics["predicate_table"] = [
            {**row, "predicate": str(row["predicate"])} for row in json_metrics["predicate_table"]
        ]
        json_metrics["flags"]["mixed_range_predicates"] = [str(p) for p in json_metrics["flags"]["mixed_range_predicates"]]
        for key in ("untyped_entities", "multi_typed_entities"):
            json_metrics["flags"][key] = [str(e) for e in json_metrics["flags"][key]]
        json_metrics["cross_namespace"] = {
            group: {name: [str(iri) for iri in iris] for name, iris in groups.items()}
            for group, groups in json_metrics["cross_namespace"].items()
        }
        output = {
            "files": [
                {"path": entry["path"], **summarize_file(entry["graph"]), "ignored_triple_count": entry["ignored_triple_count"]}
                for entry in per_file
            ],
            "ignored_triple_count": total_ignored,
            "data": json_metrics,
        }
        if richness is not None:
            output["schema_richness"] = richness
            output["conformance"] = {
                "undeclared_classes_used": sorted(str(c) for c in conformance["undeclared_classes_used"]),
                "undeclared_properties_used": sorted(str(p) for p in conformance["undeclared_properties_used"]),
                "unpopulated_classes": sorted(str(c) for c in conformance["unpopulated_classes"]),
                "domain_violations": {str(p): sorted(str(s) for s in subs) for p, subs in conformance["domain_violations"].items()},
                "range_violations": {str(p): sorted(str(o) for o in objs) for p, objs in conformance["range_violations"].items()},
                "unverifiable_domain": {str(p): n for p, n in conformance["unverifiable_domain"].items()},
                "unverifiable_range": {str(p): n for p, n in conformance["unverifiable_range"].items()},
            }
        print(json.dumps(output, indent=2))
        return

    print(f"=== Data quality report: {len(paths)} file(s){' + ' + args.ontology if args.ontology else ''} ===")
    print()
    print_per_file_table(per_file)
    print_data_report(f"{len(paths)} file(s) combined", total_ignored, data_metrics, combined_graph, top=args.top)
    if richness is not None:
        print()
        print_schema_richness(richness, args.ontology)
        print_conformance(conformance, combined_graph, top=args.top)


def run_tool():
    main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"])


if __name__ == "__main__":
    run_tool()
