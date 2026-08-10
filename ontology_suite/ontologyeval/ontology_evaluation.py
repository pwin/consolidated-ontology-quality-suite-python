"""Evaluate the quality of a hand-authored OWL2 ontology - the schema
actually crafted to model a domain - as opposed to ontology_quality.py's
schema, which is *induced* from an aggregated tarql-sketch data graph.

Real ontologies are usually split across files via `owl:imports` (e.g. a
domain module importing a shared upper ontology, or reusing vocabulary
modules like FOAF or SKOS). By default this script resolves `owl:imports`
transitively - looking first for a local copy in `--import-dir`, falling
back to fetching the import IRI over HTTP only if `--allow-network` is given
- and merges everything into one graph representing the ontology's full
transitive closure. Every metric below is then computed over that merged
graph as a whole; imported definitions are not treated any differently from
the main ontology's own, per the brief ("just deal with the transitive
closure"). Pass `--exclude-imports` to evaluate the main file alone instead
- owl:imports are then neither fetched nor merged, though any subclass/
domain/range triples the main file asserts *about* an imported term (e.g.
`ex:Person rdfs:subClassOf core:Agent`) still count, since that triple lives
in the main file regardless of whether `core:Agent`'s own definition does.

Metrics, adapted from OQuaRE (Duque-Ramos et al.) and OntoQA (Tartir et al.):

  Schema richness (OntoQA):
    AR - Attribute Richness    = attribute slots / number of classes
    RR - Relationship Richness = object properties / (object properties + subclass edges)
    IR - Inheritance Richness  = subclass edges / number of classes
  Instance richness (OntoQA) - only meaningful if the ontology itself asserts
  individuals, which most pure-TBox ontologies don't:
    CR - Class Richness        = classes with >=1 asserted instance / number of classes
    AP - Average Population    = typed instances / number of classes

  Per-class structural metrics (OQuaRE, Chidamber & Kemerer-derived) - each
  computed per class, straight from the ontology's OWN rdfs:subClassOf/
  domain/range axioms (no induction needed - a real ontology declares these
  directly), and shown as one row of the per-class table:
    inst - instance count            = asserted individuals of this class
    DIT  - Depth of Inheritance Tree = longest rdfs:subClassOf chain up to a root
    NOC  - Number Of Children        = direct subclasses
    NAC  - Number of Ancestor Classes = all superclasses, direct and transitive
    CBO  - Coupling Between Object classes = other classes reachable through a
           property this class is the domain or range of (excludes itself)
    WMC  - Weighted Methods per Class = properties declared directly on this
           class (domain includes it); "weighted" only in the sense that OQuaRE
           borrows the name from Chidamber & Kemerer - each property counts as 1
    RFC  - Response For a Class      = WMC plus properties inherited via NAC's
           ancestors (the full set of properties an instance of this class responds to)
    tangled (flag, not a column)     = the class has more than one direct parent

  gist:domainIncludes/rangeIncludes (Semantic Arts' gist ontology) are folded
    in alongside rdfs:domain/range for all of the above: they're annotation
    properties so they carry no OWL entailment, but they still record the
    designer's intended domain/range and so are just as informative for
    WMC/RFC/CBO and AR/RR here. Matched by local name, not a hardcoded gist
    namespace IRI, since gist has been published under more than one over
    the years. The report breaks out how many domain/range
    associations came from each source.

  OWL2 expressivity - constructs a data-induced schema could never produce,
  so their presence (or absence) is itself a quality signal: equivalentClass/
  equivalentProperty, disjointWith, inverseOf, property characteristics
  (Functional/InverseFunctional/Transitive/Symmetric/Asymmetric/Reflexive/
  Irreflexive), and restriction usage (someValuesFrom/allValuesFrom/hasValue/
  cardinality/qualifiedCardinality).

  Documentation coverage: rdfs:label/rdfs:comment/skos:definition/
  dcterms:description coverage on classes and properties.

  Anonymous class expressions: complex expressions used inline as a
  superclass (e.g. `[ owl:intersectionOf (...) ; a owl:Class ]`) are
  legitimately typed owl:Class, so they'd otherwise show up as unreadable
  blank-node rows in the per-class table. They're pulled out of that table
  and instead reported by kind (restriction/unionOf/intersectionOf/oneOf/
  complementOf) together with the named class whose definition they're part
  of, found by walking backward through the graph to the nearest named
  subject (handles direct use and one or more levels of RDF-list/blank-node
  nesting). Plain owl:Restriction blank nodes used directly as a
  `rdfs:subClassOf` value are unaffected by this - they were never counted
  as classes in the first place, only as `restriction_subclass_count`.

  Lint-style flags: naming-convention violations (classes not
  UpperCamelCase, properties not lowerCamelCase), deprecated terms, cyclic
  rdfs:subClassOf chains, and the same cross-namespace-duplicate-name check
  used by graph_quality.py (importing several vocabularies commonly
  reintroduces a same-named class/property).

Scope: only direct rdfs:domain/rdfs:range/rdfs:subClassOf triples and the
OWL2 constructs listed above are read. Full OWL2 DL reasoning (e.g. via a
real reasoner) is out of scope - this is a lint/metrics tool, not a
consistency checker.
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import rdflib
from rdflib import OWL, RDF, RDFS, XSD
from rdflib.namespace import DCTERMS, SKOS
from rdflib.term import BNode, Literal, URIRef

from .. import io_utils

ANNOTATION_PREDICATES = {RDFS.label, RDFS.comment, SKOS.definition, DCTERMS.description}
PROPERTY_TYPES = {OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property}
PROPERTY_CHARACTERISTICS = [
    OWL.FunctionalProperty,
    OWL.InverseFunctionalProperty,
    OWL.TransitiveProperty,
    OWL.SymmetricProperty,
    OWL.AsymmetricProperty,
    OWL.ReflexiveProperty,
    OWL.IrreflexiveProperty,
]
RESTRICTION_FACETS = [
    "someValuesFrom", "allValuesFrom", "hasValue",
    "cardinality", "minCardinality", "maxCardinality",
    "qualifiedCardinality", "minQualifiedCardinality", "maxQualifiedCardinality",
]
META_TYPES = {OWL.Class, RDFS.Class, OWL.Ontology, OWL.Restriction, OWL.NamedIndividual} | PROPERTY_TYPES
DEFAULT_IMPORT_GLOBS = "*.ttl,*.turtle,*.owl,*.rdf"


def local_name(uri):
    """The fragment/last-path-segment of a URI - used for naming-convention
    and cross-namespace-duplicate checks."""
    text = str(uri)
    for sep in ("#", "/"):
        if sep in text:
            tail = text.rsplit(sep, 1)[1]
            if tail:
                return tail
    return text


def _parse_file(path, *, allow_network: bool = True):
    """Parses `path` -- a local file, an http(s) URL, or either
    gzip-compressed -- into a fresh graph. `allow_network` defaults to
    True here: a path the caller named explicitly (the main `--ontology`
    argument, or a `--import-dir` candidate) is something the user already
    consented to by naming it; only `owl:imports` targets *discovered*
    while resolving imports are gated behind `--allow-network` (see
    `resolve_imports`, and `io_utils`'s own module docstring)."""
    graph = rdflib.Graph(bind_namespaces="none")
    io_utils.parse_graph(graph, path, allow_network=allow_network)
    return graph


def _declared_ontology_iris(graph):
    """IRIs by which a file can be matched for `owl:imports` resolution: its
    own ontology identity (`<IRI> a owl:Ontology`) *and* any `owl:versionIRI`
    it declares. Real-world ontologies commonly declare `owl:imports` against
    a specific *version* IRI (e.g. gist's own `owl:imports
    <.../gistCore14.1.0>` pattern) while the imported file's own identity
    subject is the timeless, unversioned base IRI (`<.../gistCore>`), with
    the version-specific IRI appearing only as that subject's `owl:versionIRI`
    value. Matching only the bare ontology-identity IRI leaves every such
    import unresolved even when the exact file is sitting right there
    locally -- caught by running this against a real vehicle ontology
    importing gist 14.1.0 by its versionIRI, which flooded out with false
    "undefined class/property" findings for every gist term used, because
    gist's ~200 declarations never actually got merged in.
    """
    ontology_subjects = set(graph.subjects(RDF.type, OWL.Ontology))
    version_iris = {v for s in ontology_subjects for v in graph.objects(s, OWL.versionIRI)}
    return ontology_subjects | version_iris


def resolve_imports(main_path, import_dir=None, allow_network=False, glob_patterns=DEFAULT_IMPORT_GLOBS):
    """Load `main_path` and transitively merge every owl:imports it (and its
    imports) reference, preferring a local file in `import_dir` (searched
    recursively) and falling back to an HTTP fetch only if `allow_network`.

    Returns (merged_graph, report) where report = {"resolved": [...], "unresolved": [...]}.
    """
    main_graph = _parse_file(main_path)
    merged = rdflib.Graph(bind_namespaces="none")
    for prefix, namespace in main_graph.namespaces():
        merged.bind(prefix, namespace)
    for triple in main_graph:
        merged.add(triple)

    search_dir = import_dir or os.path.dirname(os.path.abspath(main_path)) or "."
    candidate_files = sorted(
        {
            p
            for pattern in glob_patterns.split(",")
            for p in glob.glob(os.path.join(search_dir, "**", pattern.strip()), recursive=True)
        }
        - {os.path.abspath(main_path)}
    )

    visited = {str(iri) for iri in _declared_ontology_iris(main_graph)}
    pending = {str(o) for o in main_graph.objects(None, OWL.imports)} - visited
    resolved, unresolved = [], []

    while pending:
        iri = pending.pop()
        if iri in visited:
            continue
        visited.add(iri)

        found_graph, found_source = None, None
        for candidate in candidate_files:
            try:
                candidate_graph = _parse_file(candidate)
            except Exception:
                continue
            if URIRef(iri) in _declared_ontology_iris(candidate_graph):
                found_graph, found_source = candidate_graph, candidate
                break

        if found_graph is None and allow_network:
            try:
                candidate_graph = rdflib.Graph(bind_namespaces="none")
                candidate_graph.parse(iri)
                found_graph, found_source = candidate_graph, "network"
            except Exception:
                found_graph = None

        if found_graph is None:
            unresolved.append(iri)
            continue

        for prefix, namespace in found_graph.namespaces():
            merged.bind(prefix, namespace)
        for triple in found_graph:
            merged.add(triple)
        resolved.append({"iri": iri, "source": found_source})

        for further in found_graph.objects(None, OWL.imports):
            if str(further) not in visited:
                pending.add(str(further))

    return merged, {"resolved": resolved, "unresolved": sorted(unresolved), "excluded": [], "network_allowed": allow_network}


def load_without_imports(main_path):
    """Evaluate only the main ontology file itself - owl:imports are neither
    fetched nor merged, matching --exclude-imports. Any owl:imports present
    are still listed in the report (as "excluded"), just not acted on, so the
    report doesn't silently hide that they exist.
    """
    graph = _parse_file(main_path)
    excluded = sorted({str(o) for o in graph.objects(None, OWL.imports)})
    return graph, {"resolved": [], "unresolved": [], "excluded": excluded, "network_allowed": False}


def _ancestors(cls, parents_of, memo, visiting=frozenset(), cyclic=None):
    if cls in memo:
        return memo[cls]
    if cls in visiting:
        if cyclic is not None:
            cyclic.add(cls)
        return set()
    result = set()
    for parent in parents_of.get(cls, ()):
        result.add(parent)
        result |= _ancestors(parent, parents_of, memo, visiting | {cls}, cyclic)
    memo[cls] = result
    return result


def _depth(cls, parents_of, memo, visiting=frozenset()):
    if cls in memo:
        return memo[cls]
    if cls in visiting:
        return 0
    parents = parents_of.get(cls, ())
    memo[cls] = 0 if not parents else 1 + max(_depth(p, parents_of, memo, visiting | {cls}) for p in parents)
    return memo[cls]


def _find_owning_named_term(node, graph, max_hops=4):
    """Walk backward from a blank node to the nearest named (URIRef) subject
    that references it - directly, or through intermediate blank nodes such
    as RDF list cells inside an owl:unionOf/intersectionOf. This is how an
    anonymous class expression (e.g. `[ owl:intersectionOf (...) ; a owl:Class ]`
    used as a superclass) gets tied back to the named class it's part of the
    definition of. Returns the named term, or None if none is found within
    `max_hops` (e.g. the node is unreferenced, or only reachable via a cycle).
    """
    frontier, seen = {node}, {node}
    for _ in range(max_hops):
        next_frontier = set()
        for n in frontier:
            for s in graph.subjects(None, n):
                if isinstance(s, URIRef):
                    return s
                if s not in seen:
                    seen.add(s)
                    next_frontier.add(s)
        frontier = next_frontier
        if not frontier:
            break
    return None


def _expression_kind(node, graph):
    """A short human label for what kind of anonymous class expression `node` is."""
    if (node, RDF.type, OWL.Restriction) in graph:
        return "restriction"
    if (node, OWL.unionOf, None) in graph:
        return "unionOf"
    if (node, OWL.intersectionOf, None) in graph:
        return "intersectionOf"
    if (node, OWL.oneOf, None) in graph:
        return "oneOf"
    if (node, OWL.complementOf, None) in graph:
        return "complementOf"
    return "anonymous class"


def collect_schema(graph):
    """Pull the ontology's own declarations straight out of the merged graph - no induction."""
    classes = set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))

    properties = set()
    property_kind = {}
    for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property):
        for p in graph.subjects(RDF.type, kind):
            properties.add(p)
            property_kind.setdefault(p, kind)

    domain = defaultdict(set)
    range_ = defaultdict(set)
    domain_range_sources = Counter()

    for p, c in graph.subject_objects(RDFS.domain):
        domain[p].add(c)
        properties.add(p)
        domain_range_sources["domain_formal"] += 1
    for p, c in graph.subject_objects(RDFS.range):
        range_[p].add(c)
        properties.add(p)
        domain_range_sources["range_formal"] += 1

    # gist:domainIncludes/rangeIncludes (Semantic Arts' gist ontology) are annotation
    # properties, not rdfs:domain/range, so they don't cause any OWL entailment - but
    # they're exactly how gist-style ontologies record the designer's intended
    # domain/range without gist's stricter multiple-inheritance-averse modelling
    # forcing every use of a property into a single rdfs:domain/range axiom. Matched
    # by local name rather than a hardcoded namespace IRI, since gist has been
    # published under more than one namespace IRI over the years.
    domain_includes_predicates = {p for p in graph.predicates() if local_name(p) == "domainIncludes"}
    range_includes_predicates = {p for p in graph.predicates() if local_name(p) == "rangeIncludes"}
    for pred in domain_includes_predicates:
        for prop, cls in graph.subject_objects(pred):
            domain[prop].add(cls)
            properties.add(prop)
            domain_range_sources["domain_informal"] += 1
    for pred in range_includes_predicates:
        for prop, cls in graph.subject_objects(pred):
            range_[prop].add(cls)
            properties.add(prop)
            domain_range_sources["range_informal"] += 1

    parents_of = defaultdict(set)
    children_of = defaultdict(set)
    restriction_subclass_count = 0
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            parents_of[child].add(parent)
            children_of[parent].add(child)
            classes.add(child)
            classes.add(parent)
        else:
            restriction_subclass_count += 1

    # Anonymous class expressions (e.g. `[ owl:intersectionOf (...) ; a owl:Class ]`
    # used inline as a superclass) are legitimately typed owl:Class, so they end up
    # in `classes` above - but they aren't something a human reviewer can act on by
    # name, so they're split out and instead tied back to whichever named class
    # they're part of the definition of (see _find_owning_named_term).
    named_classes = {c for c in classes if isinstance(c, URIRef)}
    anonymous_classes = {c for c in classes if isinstance(c, BNode)}
    anonymous_class_info = [
        {"node": node, "kind": _expression_kind(node, graph), "owner": _find_owning_named_term(node, graph)}
        for node in anonymous_classes
    ]

    annotated = set()
    for pred in ANNOTATION_PREDICATES:
        annotated |= set(graph.subjects(pred, None))

    entity_types = defaultdict(set)
    for s, o in graph.subject_objects(RDF.type):
        if o in META_TYPES or o not in classes:
            continue
        entity_types[s].add(o)

    equivalent_classes = list(graph.subject_objects(OWL.equivalentClass))
    disjoint_with = list(graph.subject_objects(OWL.disjointWith))
    equivalent_properties = list(graph.subject_objects(OWL.equivalentProperty))
    inverse_of = list(graph.subject_objects(OWL.inverseOf))

    property_characteristics = Counter()
    for characteristic in PROPERTY_CHARACTERISTICS:
        property_characteristics[local_name(characteristic)] = len(set(graph.subjects(RDF.type, characteristic)))

    restrictions = list(graph.subjects(RDF.type, OWL.Restriction))
    restriction_facet_counts = Counter()
    for restriction in restrictions:
        for facet in RESTRICTION_FACETS:
            if (restriction, getattr(OWL, facet), None) in graph:
                restriction_facet_counts[facet] += 1

    deprecated_classes = {c for c in classes if (c, OWL.deprecated, Literal(True)) in graph}
    deprecated_properties = {p for p in properties if (p, OWL.deprecated, Literal(True)) in graph}

    return {
        "classes": classes,
        "named_classes": named_classes,
        "anonymous_classes": anonymous_classes,
        "anonymous_class_info": anonymous_class_info,
        "properties": properties,
        "property_kind": property_kind,
        "domain": domain,
        "range": range_,
        "domain_range_sources": dict(domain_range_sources),
        "parents_of": parents_of,
        "children_of": children_of,
        "restriction_subclass_count": restriction_subclass_count,
        "annotated": annotated,
        "entity_types": entity_types,
        "equivalent_classes": equivalent_classes,
        "disjoint_with": disjoint_with,
        "equivalent_properties": equivalent_properties,
        "inverse_of": inverse_of,
        "property_characteristics": property_characteristics,
        "restriction_count": len(restrictions),
        "restriction_facet_counts": restriction_facet_counts,
        "deprecated_classes": deprecated_classes,
        "deprecated_properties": deprecated_properties,
    }


def classify_property(p, schema):
    kind = schema["property_kind"].get(p)
    if kind == OWL.ObjectProperty:
        return "object"
    if kind == OWL.DatatypeProperty:
        return "datatype"
    if kind == OWL.AnnotationProperty:
        return "annotation"
    ranges = schema["range"].get(p, set())
    if ranges and all(str(r).startswith(str(XSD)) or r == RDFS.Literal for r in ranges):
        return "datatype"
    if ranges:
        return "object"
    return "unknown"


def cross_namespace_groups(terms):
    by_name = defaultdict(set)
    for term in terms:
        if isinstance(term, URIRef):
            by_name[local_name(term)].add(term)
    return {name: sorted(iris, key=str) for name, iris in by_name.items() if len(iris) > 1}


def compute_metrics(schema):
    classes, properties = schema["named_classes"], schema["properties"]
    domain, range_ = schema["domain"], schema["range"]
    parents_of, children_of = schema["parents_of"], schema["children_of"]

    object_properties, datatype_properties, annotation_properties = set(), set(), set()
    for p in properties:
        kind = classify_property(p, schema)
        {"object": object_properties, "datatype": datatype_properties, "annotation": annotation_properties}.get(
            kind, set()
        ).add(p)

    subclass_edge_count = sum(len(parents) for parents in parents_of.values())
    attribute_slots = sum((len(domain.get(p, set()) & classes) or 1) for p in datatype_properties)
    typed_instance_count = len(schema["entity_types"])
    populated_classes = {c for types in schema["entity_types"].values() for c in types}
    instances_by_class = defaultdict(set)
    for entity, types in schema["entity_types"].items():
        for cls in types:
            instances_by_class[cls].add(entity)

    richness = {
        "attribute_richness": round(attribute_slots / len(classes), 3) if classes else 0.0,
        "relationship_richness": round(len(object_properties) / (len(object_properties) + subclass_edge_count), 3)
        if (object_properties or subclass_edge_count)
        else 0.0,
        "inheritance_richness": round(subclass_edge_count / len(classes), 3) if classes else 0.0,
    }
    has_individuals = bool(schema["entity_types"])
    instance_richness = None
    if has_individuals:
        instance_richness = {
            "class_richness": round(len(populated_classes) / len(classes), 3) if classes else 0.0,
            "average_population": round(typed_instance_count / len(classes), 3) if classes else 0.0,
        }

    ancestor_memo, dit_memo = {}, {}
    cyclic = set()
    per_class = {}
    for cls in classes:
        ancestors = _ancestors(cls, parents_of, ancestor_memo, cyclic=cyclic)
        own_props = {p for p in properties if cls in domain.get(p, set())}
        inherited_props = {p for p in properties if domain.get(p, set()) & ancestors}
        coupled = set()
        for p in properties:
            if cls in domain.get(p, set()):
                coupled |= {c for c in range_.get(p, set()) if isinstance(c, URIRef)} - {cls}
            if cls in range_.get(p, set()):
                coupled |= {c for c in domain.get(p, set()) if isinstance(c, URIRef)} - {cls}
        per_class[cls] = {
            "instances": len(instances_by_class.get(cls, set())),
            "dit": _depth(cls, parents_of, dit_memo),
            "noc": len(children_of.get(cls, set())),
            "nac": len(ancestors),
            "cbo": len(coupled),
            "wmc": len(own_props),
            "rfc": len(own_props | inherited_props),
            "tangled": len(parents_of.get(cls, set())) > 1,
            "annotated": cls in schema["annotated"],
            "deprecated": cls in schema["deprecated_classes"],
        }

    annotated_property_count = sum(1 for p in properties if p in schema["annotated"])
    annotated_class_count = sum(1 for m in per_class.values() if m["annotated"])

    bad_class_names = {
        c for c in classes if isinstance(c, URIRef) and local_name(c) and not local_name(c)[0].isupper()
    }
    bad_property_names = {
        p for p in properties if isinstance(p, URIRef) and local_name(p) and not local_name(p)[0].islower()
    }

    return {
        "sizes": {
            "class_count": len(classes),
            "anonymous_class_count": len(schema["anonymous_classes"]),
            "property_count": len(properties),
            "object_property_count": len(object_properties),
            "datatype_property_count": len(datatype_properties),
            "annotation_property_count": len(annotation_properties),
            "subclass_edge_count": subclass_edge_count,
        },
        "richness": richness,
        "instance_richness": instance_richness,
        "anonymous_classes": schema["anonymous_class_info"],
        "completeness": {
            "annotated_class_count": annotated_class_count,
            "annotated_property_count": annotated_property_count,
            "domain_range_sources": schema["domain_range_sources"],
        },
        "expressivity": {
            "equivalent_class_count": len(schema["equivalent_classes"]),
            "disjoint_with_count": len(schema["disjoint_with"]),
            "equivalent_property_count": len(schema["equivalent_properties"]),
            "inverse_of_count": len(schema["inverse_of"]),
            "property_characteristics": dict(schema["property_characteristics"]),
            "restriction_count": schema["restriction_count"],
            "restriction_facet_counts": dict(schema["restriction_facet_counts"]),
            "restriction_subclass_count": schema["restriction_subclass_count"],
        },
        "per_class": per_class,
        "flags": {
            "cyclic_classes": sorted(cyclic, key=str),
            "bad_class_names": sorted(bad_class_names, key=str),
            "bad_property_names": sorted(bad_property_names, key=str),
            "deprecated_classes": sorted(schema["deprecated_classes"], key=str),
            "deprecated_properties": sorted(schema["deprecated_properties"], key=str),
            "cross_namespace_types": cross_namespace_groups(classes),
            "cross_namespace_properties": cross_namespace_groups(properties),
        },
    }


def _label(term, graph):
    if isinstance(term, Literal):
        return f'"{term}"'
    if isinstance(term, BNode):
        return f"_:{term}"
    return graph.qname(term)


def print_imports_report(report):
    print("-- Imports (transitive closure) --")
    if not report["resolved"] and not report["unresolved"] and not report.get("excluded"):
        print("(no owl:imports found)")
        print()
        return
    for entry in report["resolved"]:
        print(f"resolved:   {entry['iri']}  <-  {entry['source']}")
    for iri in report["unresolved"]:
        hint = "network fetching disabled; re-run with --allow-network" if not report["network_allowed"] else "fetch failed"
        print(f"unresolved: {iri}  ({hint})")
    for iri in report.get("excluded", []):
        print(f"excluded:   {iri}  (--exclude-imports given; not fetched or merged)")
    print()


def print_report(path, report, metrics, graph, top=20):
    sizes, richness, completeness, expressivity = (
        metrics["sizes"],
        metrics["richness"],
        metrics["completeness"],
        metrics["expressivity"],
    )

    excluded_note = f", {len(report['excluded'])} excluded" if report.get("excluded") else ""
    print(f"=== Ontology evaluation: {path} ===")
    print(f"({len(report['resolved'])} import(s) resolved, {len(report['unresolved'])} unresolved{excluded_note})")
    print()

    print_imports_report(report)

    print("-- Size --")
    anonymous_note = f" (+ {sizes['anonymous_class_count']} anonymous class expression(s), see below)" if sizes["anonymous_class_count"] else ""
    print(
        f"Classes: {sizes['class_count']}{anonymous_note}   Properties: {sizes['property_count']} "
        f"({sizes['object_property_count']} object, {sizes['datatype_property_count']} datatype, "
        f"{sizes['annotation_property_count']} annotation)   Subclass edges: {sizes['subclass_edge_count']}"
    )
    sources = completeness["domain_range_sources"]
    if sources:
        print(
            f"Domain/range associations: "
            f"{sources.get('domain_formal', 0)} formal domain (rdfs:domain) + {sources.get('domain_informal', 0)} informal (gist:domainIncludes), "
            f"{sources.get('range_formal', 0)} formal range (rdfs:range) + {sources.get('range_informal', 0)} informal (gist:rangeIncludes)"
        )
    print()

    print("-- Schema richness (OntoQA) --")
    print(f"Attribute Richness (AR):     {richness['attribute_richness']:<6}  attribute slots per class")
    print(f"Relationship Richness (RR):  {richness['relationship_richness']:<6}  object properties vs. object properties + subclass edges")
    print(f"Inheritance Richness (IR):   {richness['inheritance_richness']:<6}  avg subclasses per class")
    print()

    print("-- Instance richness (OntoQA) --")
    if metrics["instance_richness"] is None:
        print("n/a - this ontology asserts no individuals of its own classes (expected for a pure TBox)")
    else:
        ir = metrics["instance_richness"]
        print(f"Class Richness (CR):         {ir['class_richness']:<6}  declared classes with >=1 asserted instance")
        print(f"Average Population (AP):     {ir['average_population']:<6}  typed instances per class")
    print()

    print("-- Documentation coverage --")
    print(
        f"Classes with rdfs:label/comment or skos:definition/dcterms:description: "
        f"{completeness['annotated_class_count']}/{sizes['class_count']}"
    )
    print(
        f"Properties with the same: "
        f"{completeness['annotated_property_count']}/{sizes['property_count']}"
    )
    print()

    print("-- OWL2 expressivity --")
    print(f"owl:equivalentClass axioms:    {expressivity['equivalent_class_count']}")
    print(f"owl:disjointWith axioms:       {expressivity['disjoint_with_count']}")
    print(f"owl:equivalentProperty axioms: {expressivity['equivalent_property_count']}")
    print(f"owl:inverseOf axioms:          {expressivity['inverse_of_count']}")
    characteristics = ", ".join(f"{k}={v}" for k, v in expressivity["property_characteristics"].items() if v) or "(none)"
    print(f"Property characteristics:      {characteristics}")
    print(f"owl:Restriction usage:         {expressivity['restriction_count']} restriction(s), {expressivity['restriction_subclass_count']} used as a subclass axiom")
    facets = ", ".join(f"{k}={v}" for k, v in expressivity["restriction_facet_counts"].items() if v) or "(none)"
    print(f"Restriction facets:            {facets}")
    print()

    print("-- Anonymous class expressions --")
    if metrics["anonymous_classes"]:
        for entry in metrics["anonymous_classes"]:
            owner_label = graph.qname(entry["owner"]) if entry["owner"] is not None else "no named owner found"
            print(f"{entry['kind']:<16} part of the definition of {owner_label}")
    else:
        print("(none - every owl:Class declaration in this ontology is a named term)")
    print()

    print("-- Per-class structural metrics (OQuaRE) --")
    print(
        "inst=instances  dit=Depth of Inheritance Tree  noc=Number Of Children  "
        "nac=Number of Ancestor Classes  cbo=Coupling Between Object classes  "
        "wmc=Weighted Methods per Class (own properties)  rfc=Response For a Class (wmc + inherited)"
    )
    rows = sorted(metrics["per_class"].items(), key=lambda kv: kv[1]["rfc"], reverse=True)
    if top > 0:
        rows = rows[:top]
    if rows:
        print(f"{'class':<28}{'inst':>6}{'dit':>5}{'noc':>5}{'nac':>5}{'cbo':>5}{'wmc':>5}{'rfc':>5}  flags")
        for cls, m in rows:
            flags = []
            if m["tangled"]:
                flags.append("tangled")
            if m["deprecated"]:
                flags.append("deprecated")
            if not m["annotated"]:
                flags.append("undocumented")
            print(
                f"{_label(cls, graph):<28}{m['instances']:>6}{m['dit']:>5}{m['noc']:>5}{m['nac']:>5}"
                f"{m['cbo']:>5}{m['wmc']:>5}{m['rfc']:>5}  {', '.join(flags)}"
            )
        omitted = len(metrics["per_class"]) - len(rows)
        if omitted > 0:
            print(f"... (+{omitted} more, use --top 0 to show all)")
    else:
        print("(no classes)")
    print()

    print("-- Quality flags --")
    flags, raised = metrics["flags"], False
    if flags["cyclic_classes"]:
        raised = True
        names = ", ".join(_label(c, graph) for c in flags["cyclic_classes"])
        print(f"[!] {len(flags['cyclic_classes'])} class(es) involved in a cyclic rdfs:subClassOf chain: {names}")
    if flags["bad_class_names"]:
        raised = True
        names = ", ".join(_label(c, graph) for c in flags["bad_class_names"][:10])
        print(f"[!] {len(flags['bad_class_names'])} class(es) not UpperCamelCase: {names}")
    if flags["bad_property_names"]:
        raised = True
        names = ", ".join(_label(p, graph) for p in flags["bad_property_names"][:10])
        print(f"[!] {len(flags['bad_property_names'])} propert(y/ies) not lowerCamelCase: {names}")
    if flags["deprecated_classes"] or flags["deprecated_properties"]:
        raised = True
        names = ", ".join(_label(c, graph) for c in flags["deprecated_classes"] + flags["deprecated_properties"])
        print(f"[!] {len(flags['deprecated_classes']) + len(flags['deprecated_properties'])} deprecated term(s) still present: {names}")
    if flags["cross_namespace_types"]:
        raised = True
        for name, iris in flags["cross_namespace_types"].items():
            print(f"[!] type '{name}' defined in {len(iris)} namespaces: {', '.join(graph.qname(i) for i in iris)}")
    if flags["cross_namespace_properties"]:
        raised = True
        for name, iris in flags["cross_namespace_properties"].items():
            print(f"[!] property '{name}' defined in {len(iris)} namespaces: {', '.join(graph.qname(i) for i in iris)}")
    if report["unresolved"]:
        raised = True
        print(f"[!] {len(report['unresolved'])} unresolved import(s): {', '.join(report['unresolved'])}")
    if not raised:
        print("(none)")


def main(argv):
    parser = argparse.ArgumentParser(
        prog="ontology-evaluation",
        description="Evaluates a hand-authored OWL2/RDFS ontology (OQuaRE/OntoQA-inspired), "
        "resolving owl:imports transitively and evaluating the merged closure as a whole.",
        epilog="version 0.1",
    )
    parser.add_argument("ontology", help="the main ontology turtle/RDF file to evaluate")
    parser.add_argument(
        "--import-dir",
        default=None,
        help="directory to search recursively for local copies of imported ontologies "
        "(default: the main ontology file's own directory)",
    )
    parser.add_argument(
        "--import-pattern",
        default=DEFAULT_IMPORT_GLOBS,
        help=f"comma-separated glob(s) used to find candidate import files (default: {DEFAULT_IMPORT_GLOBS})",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="fetch an owl:imports IRI over HTTP if no local copy is found (off by default)",
    )
    parser.add_argument(
        "--exclude-imports",
        action="store_true",
        help="evaluate only the main ontology file itself - owl:imports are neither fetched nor merged "
        "(--import-dir/--import-pattern/--allow-network are ignored when this is set)",
    )
    parser.add_argument("--top", type=int, default=20, help="max rows in the per-class table, 0 for all (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text report")
    args = parser.parse_args(argv)

    if args.exclude_imports:
        graph, report = load_without_imports(args.ontology)
    else:
        graph, report = resolve_imports(args.ontology, args.import_dir, args.allow_network, args.import_pattern)
    schema = collect_schema(graph)
    metrics = compute_metrics(schema)

    if args.json:
        json_metrics = {
            "sizes": metrics["sizes"],
            "richness": metrics["richness"],
            "instance_richness": metrics["instance_richness"],
            "completeness": metrics["completeness"],
            "expressivity": metrics["expressivity"],
            "anonymous_classes": [
                {
                    "kind": entry["kind"],
                    "owner": graph.qname(entry["owner"]) if entry["owner"] is not None else None,
                }
                for entry in metrics["anonymous_classes"]
            ],
            "per_class": {graph.qname(c): m for c, m in metrics["per_class"].items()},
            "flags": {
                "cyclic_classes": [_label(c, graph) for c in metrics["flags"]["cyclic_classes"]],
                "bad_class_names": [_label(c, graph) for c in metrics["flags"]["bad_class_names"]],
                "bad_property_names": [_label(p, graph) for p in metrics["flags"]["bad_property_names"]],
                "deprecated_classes": [_label(c, graph) for c in metrics["flags"]["deprecated_classes"]],
                "deprecated_properties": [_label(p, graph) for p in metrics["flags"]["deprecated_properties"]],
                "cross_namespace_types": {
                    name: [str(i) for i in iris] for name, iris in metrics["flags"]["cross_namespace_types"].items()
                },
                "cross_namespace_properties": {
                    name: [str(i) for i in iris] for name, iris in metrics["flags"]["cross_namespace_properties"].items()
                },
            },
            "imports": report,
        }
        print(json.dumps(json_metrics, indent=2))
    else:
        print_report(args.ontology, report, metrics, graph, top=args.top)


def run_tool():
    main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"])


if __name__ == "__main__":
    run_tool()
