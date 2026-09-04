#!/usr/bin/env python3
"""
extract_ontology_data.py  (rdflib-backed edition)

Parses a Turtle-serialized OWL ontology into a JSON data model consumed
by build_documentation.py / documentation-template.html.

Turtle parsing itself is delegated to rdflib via turtle_parser.py,
which adapts rdflib's graph into the same (subject, predicate, object)
triple list and typed terms (IRI, BNode, Literal, Collection) this file
has always consumed - see turtle_parser.py's module docstring for that
adapter's details. All extraction below - classes, properties,
annotations, subClassOf, equivalentClass, disjointWith,
domain/range/subPropertyOf - is real graph traversal over that triple
store, not string matching. In particular:

  - rdfs:subClassOf and owl:equivalentClass are resolved properly for
    BOTH named-class values and anonymous (blank-node) OWL restriction/
    intersectionOf/unionOf expressions, at arbitrary nesting depth, by
    walking the actual blank-node structure - not a regex approximating
    one specific formatting convention.
  - "a" and "rdf:type" are handled identically, because rdflib
    normalizes both to the same rdf:type predicate at parse time.
  - Compact one-line declarations, multi-line block declarations, CRLF
    line endings, and statements that don't close with a period alone
    on its own line are all handled correctly, because rdflib is a
    conformant Turtle parser rather than guessing from text layout.
  - Object/datatype properties also report gist:domainIncludes and
    gist:rangeIncludes (rdfs:subPropertyOf skos:scopeNote in gist -
    non-logical annotation hints, not OWL axioms) alongside
    rdfs:domain/rdfs:range, as separate `domainIncludes`/
    `rangeIncludes` JSON fields - gist itself, and ontologies following
    its modeling convention, routinely define a property with only
    these annotations and no rdfs:domain/range at all, precisely
    because gist recommends against rdfs:domain/range for most
    properties (it forces every use of a property into a single
    global class-membership inference, which is usually too strong).
    Conflating the two into one field would misrepresent an annotation
    hint as a logical axiom, so they stay distinct end to end - see the
    template's property cards and §2.3 diagram, which render
    domain/range-only edges as solid and domainIncludes/rangeIncludes
    edges as dashed.

Comment-based section headers (`# Classes: <Title>`) are the one thing
still extracted via a small, separately-scoped text pass, rather than
graph traversal - comments carry no RDF triples, so there is nothing
for a parser to hand back for them. This remains a best-effort,
cosmetic-only feature, exactly as before.

USAGE
-----
    python3 extract_ontology_data.py \\
        --ontology path/to/your-ontology.ttl \\
        [--instances path/to/your-abox.ttl] \\
        [--ref path/to/imported-ontology-1.ttl] \\
        [--prefix veh] \\
        --out ontology_doc_data.json
"""

import re
import json
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .. import io_utils
from .turtle_parser import parse_turtle, IRI, BNode, Literal, Collection, RDF_TYPE


# ---------------------------------------------------------------------------
# Vocabulary constants
# ---------------------------------------------------------------------------

RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
GIST = "https://w3id.org/semanticarts/ns/ontology/gist/"

OWL_CLASS = IRI(OWL + "Class")
OWL_OBJECT_PROPERTY = IRI(OWL + "ObjectProperty")
OWL_DATATYPE_PROPERTY = IRI(OWL + "DatatypeProperty")
OWL_ONTOLOGY = IRI(OWL + "Ontology")
OWL_RESTRICTION = IRI(OWL + "Restriction")
OWL_SUBCLASSOF = IRI(RDFS + "subClassOf")
OWL_EQUIVALENTCLASS = IRI(OWL + "equivalentClass")
OWL_DISJOINTWITH = IRI(OWL + "disjointWith")
OWL_ONPROPERTY = IRI(OWL + "onProperty")
OWL_SOMEVALUESFROM = IRI(OWL + "someValuesFrom")
OWL_ALLVALUESFROM = IRI(OWL + "allValuesFrom")
OWL_HASVALUE = IRI(OWL + "hasValue")
OWL_CARDINALITY = IRI(OWL + "cardinality")
OWL_MINCARDINALITY = IRI(OWL + "minCardinality")
OWL_MAXCARDINALITY = IRI(OWL + "maxCardinality")
OWL_QUALIFIEDCARDINALITY = IRI(OWL + "qualifiedCardinality")
OWL_MINQUALIFIEDCARDINALITY = IRI(OWL + "minQualifiedCardinality")
OWL_MAXQUALIFIEDCARDINALITY = IRI(OWL + "maxQualifiedCardinality")
OWL_INTERSECTIONOF = IRI(OWL + "intersectionOf")
OWL_UNIONOF = IRI(OWL + "unionOf")
OWL_COMPLEMENTOF = IRI(OWL + "complementOf")
RDFS_DOMAIN = IRI(RDFS + "domain")
RDFS_RANGE = IRI(RDFS + "range")
GIST_DOMAININCLUDES = IRI(GIST + "domainIncludes")
GIST_RANGEINCLUDES = IRI(GIST + "rangeIncludes")
RDFS_SUBPROPERTYOF = IRI(RDFS + "subPropertyOf")
RDFS_LABEL = IRI(RDFS + "label")
RDFS_COMMENT = IRI(RDFS + "comment")
OWL_VERSIONIRI = IRI(OWL + "versionIRI")
OWL_VERSIONINFO = IRI(OWL + "versionInfo")
OWL_IMPORTS = IRI(OWL + "imports")

STRUCTURAL_PREFIXES = {"rdf", "rdfs", "owl", "xsd", "xml"}

LABEL_PREDICATES = [IRI(SKOS + "prefLabel"), RDFS_LABEL, IRI(DC + "title"), IRI(DCTERMS + "title")]
DEFINITION_PREDICATES = [IRI(SKOS + "definition"), RDFS_COMMENT, IRI(DC + "description"), IRI(DCTERMS + "description")]

# The language a generated document is written in. Not configurable yet; naming
# it means the choice is made once and visibly, rather than by whichever
# literal a parser happened to yield first.
DOC_LANGUAGE = "en"


def preferred_literal(values, language=DOC_LANGUAGE):
    """The best of several annotation values for one predicate.

    Taking `values[0]` is fine while every annotation is in one language,
    which was true of every fixture here and of most ontologies under test.
    It stops being fine the moment `--ref` points at a published vocabulary:
    W3C's `org.ttl` carries `rdfs:comment` in English, French, Italian and
    Spanish, and five identical runs of one docgen command produced the
    definition in three different languages. The order `graph.objects`
    returns depends on Python's per-process string hashing, so this was not
    merely a wrong choice -- it was a different wrong choice each run, in a
    document meant to be diffable.

    Preference: the wanted language, then an untagged literal (what a
    single-language ontology writes), then the first in sorted order -- so a
    vocabulary offering nothing in `language` still contributes a definition
    rather than a blank, and contributes the same one every time.
    """
    if not values:
        return None
    tagged = [v for v in values if isinstance(v, Literal)]
    for want in (language, None):
        for value in tagged:
            if getattr(value, "lang", None) == want:
                return value
    return sorted(values, key=lambda v: str(getattr(v, "value", v)))[0]
EXAMPLE_PREDICATES = [IRI(SKOS + "example")]
SCOPE_NOTE_PREDICATES = [IRI(SKOS + "scopeNote"), IRI(SKOS + "note")]


# ---------------------------------------------------------------------------
# Graph: a thin, convenient query wrapper around the triple list
# ---------------------------------------------------------------------------

class Graph:
    def __init__(self, triples):
        self.triples = triples
        self._by_subject = defaultdict(list)
        for s, p, o in triples:
            self._by_subject[s].append((p, o))

    def predicate_objects(self, subject):
        return self._by_subject.get(subject, [])

    def objects(self, subject, predicate):
        return [o for p, o in self._by_subject.get(subject, []) if p == predicate]

    def object(self, subject, predicate):
        vals = self.objects(subject, predicate)
        return vals[0] if vals else None

    def types(self, subject):
        return self.objects(subject, RDF_TYPE)

    def subjects_of_type(self, type_iri):
        return [s for s, pos in self._by_subject.items() if any(p == RDF_TYPE and o == type_iri for p, o in pos)]


def curie(term, ns_to_prefix):
    """Render an IRI as prefix:local if a known namespace matches
    (longest namespace match wins), else return the full IRI."""
    if not isinstance(term, IRI):
        return str(term)
    best = None
    for ns, prefix in ns_to_prefix.items():
        if term.startswith(ns) and (best is None or len(ns) > len(best[0])):
            best = (ns, prefix)
    if best:
        ns, prefix = best
        local = term[len(ns):]
        return f"{prefix}:{local}" if prefix else f":{local}"
    return str(term)


# ---------------------------------------------------------------------------
# Class-expression rendering: subClassOf / equivalentClass values, which
# may be a named class, or an anonymous owl:Restriction / intersectionOf /
# unionOf / complementOf blank node, at arbitrary nesting depth.
# ---------------------------------------------------------------------------

def render_class_expression(term, graph, ns_to_prefix, seen=None):
    """Returns a human-readable string for a class-expression term."""
    if seen is None:
        seen = set()

    if isinstance(term, IRI):
        return curie(term, ns_to_prefix)

    if isinstance(term, Collection):
        return " and ".join(render_class_expression(i, graph, ns_to_prefix, seen) for i in term)

    if isinstance(term, BNode):
        if term in seen:
            return "(recursive reference)"
        seen = seen | {term}

        types = set(graph.types(term))

        if OWL_RESTRICTION in types:
            prop = graph.object(term, OWL_ONPROPERTY)
            prop_str = curie(prop, ns_to_prefix) if prop else "?"

            some = graph.object(term, OWL_SOMEVALUESFROM)
            if some is not None:
                return f"({prop_str} some {render_class_expression(some, graph, ns_to_prefix, seen)})"
            allv = graph.object(term, OWL_ALLVALUESFROM)
            if allv is not None:
                return f"({prop_str} only {render_class_expression(allv, graph, ns_to_prefix, seen)})"
            hasv = graph.object(term, OWL_HASVALUE)
            if hasv is not None:
                return f"({prop_str} value {render_class_expression(hasv, graph, ns_to_prefix, seen)})"
            for card_pred, label in [
                (OWL_QUALIFIEDCARDINALITY, "exactly"), (OWL_CARDINALITY, "exactly"),
                (OWL_MINQUALIFIEDCARDINALITY, "min"), (OWL_MINCARDINALITY, "min"),
                (OWL_MAXQUALIFIEDCARDINALITY, "max"), (OWL_MAXCARDINALITY, "max"),
            ]:
                card = graph.object(term, card_pred)
                if card is not None:
                    val = card.value if isinstance(card, Literal) else str(card)
                    return f"({prop_str} {label} {val})"
            return f"(restriction on {prop_str})"

        inter = graph.object(term, OWL_INTERSECTIONOF)
        if isinstance(inter, Collection):
            return " and ".join(render_class_expression(i, graph, ns_to_prefix, seen) for i in inter)

        union = graph.object(term, OWL_UNIONOF)
        if isinstance(union, Collection):
            return "(" + " or ".join(render_class_expression(i, graph, ns_to_prefix, seen) for i in union) + ")"

        comp = graph.object(term, OWL_COMPLEMENTOF)
        if comp is not None:
            return f"(not {render_class_expression(comp, graph, ns_to_prefix, seen)})"

        return "(anonymous class expression)"

    return str(term)


def split_class_expression_terms(term, graph):
    """For a subClassOf/equivalentClass VALUE, return the list of
    'top-level conjunct' terms - i.e. if it's an intersectionOf, return
    its members; otherwise return [term] itself. Used so that a
    multi-member equivalentClass intersection can still surface each
    named-class conjunct as a separate, cross-referenceable entry,
    while restriction conjuncts get rendered as readable text."""
    if isinstance(term, BNode):
        inter = graph.object(term, OWL_INTERSECTIONOF)
        if isinstance(inter, Collection):
            return list(inter)
    return [term]


# ---------------------------------------------------------------------------
# Ontology header, namespaces, prefix detection
# ---------------------------------------------------------------------------

def extract_ontology_header(graph, ns_to_prefix):
    onto_subjects = graph.subjects_of_type(OWL_ONTOLOGY)
    if not onto_subjects:
        return {"iri": "", "label": "", "comment": "", "versionIri": "", "versionInfo": "", "imports": []}
    subj = onto_subjects[0]

    def first_annotation(preds):
        for pred in preds:
            vals = graph.objects(subj, pred)
            if vals:
                v = preferred_literal(vals)
                return v.value if isinstance(v, Literal) else str(v)
        return ""

    version_iri = graph.object(subj, OWL_VERSIONIRI)
    imports = [str(i) for i in graph.objects(subj, OWL_IMPORTS)]
    return {
        "iri": str(subj),
        "label": first_annotation(LABEL_PREDICATES),
        "comment": first_annotation(DEFINITION_PREDICATES),
        "versionIri": str(version_iri) if version_iri else "",
        "versionInfo": first_annotation([OWL_VERSIONINFO]),
        "imports": imports,
    }


def count_subject_usage(graph, ns_iri):
    """How many classes/object-properties/datatype-properties are
    declared (typed) within this namespace."""
    count = 0
    for s in graph._by_subject:
        if isinstance(s, IRI) and s.startswith(ns_iri):
            types = graph.types(s)
            if OWL_CLASS in types or OWL_OBJECT_PROPERTY in types or OWL_DATATYPE_PROPERTY in types:
                count += 1
    return count


def detect_local_namespace(graph, namespaces, explicit_prefix=None):
    """Returns (local_ns, local_prefix): the namespace URI is the
    canonical identifier used for all 'is this term local' checks
    throughout the rest of this script; local_prefix is only a display
    label. Computed together, in one pass, deliberately - an earlier
    version derived local_ns by looking up which namespace maps to
    local_prefix in a {namespace: prefix} dict built after the fact,
    which silently broke when two prefixes alias the same namespace (a
    real scenario this tool has encountered): usage counts end up tied
    between the aliases (usage is fundamentally a namespace-level
    count), and if the tie-break picked one alias while the display
    dict happened to have collapsed onto the other, `local_ns` could
    end up looked up incorrectly."""
    if explicit_prefix is not None:
        for ns in namespaces:
            if ns["prefix"] == explicit_prefix:
                return ns["uri"], explicit_prefix
        return "", explicit_prefix

    # usage is fundamentally per-namespace (a URI is a URI no matter
    # which alias spelled it), so group candidate prefixes by namespace
    # first, then pick a namespace by usage, then pick a display prefix
    # among that namespace's aliases.
    by_ns = defaultdict(list)
    for ns in namespaces:
        if ns["prefix"] in STRUCTURAL_PREFIXES or ns["prefix"] in ("skos", "dc", "dcterms", "xml"):
            continue
        by_ns[ns["uri"]].append(ns["prefix"])

    usage = {uri: count_subject_usage(graph, uri) for uri in by_ns}

    header = extract_ontology_header(graph, {})
    uri_matches_ontology_iri = set()
    if header["iri"]:
        candidates = {header["iri"], header["iri"] + "#", header["iri"] + "/"}
        uri_matches_ontology_iri = {uri for uri in by_ns if uri in candidates}

    def pick_display_prefix(uri):
        aliases = by_ns[uri]
        # prefer a non-empty alias for display purposes if there's a
        # choice, purely cosmetic (an empty/default prefix still works
        # functionally, a named one just reads better in the output)
        named = [a for a in aliases if a]
        return named[0] if named else aliases[0]

    if any(usage.values()):
        max_usage = max(usage.values())
        top_ns = [uri for uri, c in usage.items() if c == max_usage]
        if len(top_ns) == 1:
            uri = top_ns[0]
        else:
            preferred = [uri for uri in top_ns if uri in uri_matches_ontology_iri]
            uri = preferred[0] if preferred else top_ns[0]
        return uri, pick_display_prefix(uri)

    if uri_matches_ontology_iri:
        uri = next(iter(uri_matches_ontology_iri))
        return uri, pick_display_prefix(uri) if uri in by_ns else ""

    return "", None


# ---------------------------------------------------------------------------
# Section headers (comment-based, best-effort, cosmetic only - see
# module docstring)
# ---------------------------------------------------------------------------

def extract_section_headers(text):
    headers = []
    for m in re.finditer(r"^#+[ \t]*(.+?)[ \t]*$", text, re.MULTILINE):
        candidate = m.group(1).strip()
        cm = re.match(r"(?:Classes?|Section|Properties)\s*:\s*(.+)", candidate, re.IGNORECASE)
        if not cm:
            continue
        title = cm.group(1).strip()
        if len(re.sub(r"[^A-Za-z0-9]", "", title)) < 3:
            continue
        headers.append((m.start(), title))
    return headers


def section_for_subject(subj_curie, text, header_positions, cache={}):
    if subj_curie in cache:
        return cache[subj_curie]
    offset = text.find(subj_curie)
    section = "General"
    for pos, title in header_positions:
        if offset != -1 and pos < offset:
            section = title
        elif offset == -1:
            break
        else:
            break
    cache[subj_curie] = section
    return section


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract(graph, ns_to_prefix, local_ns, local_prefix, ontology_text):
    header_positions = extract_section_headers(ontology_text)
    section_cache = {}

    classes, obj_props, dt_props = [], [], []

    for subj in list(graph._by_subject.keys()):
        if not (isinstance(subj, IRI) and subj.startswith(local_ns)):
            continue
        types = set(graph.types(subj))
        is_class = OWL_CLASS in types
        is_obj_prop = OWL_OBJECT_PROPERTY in types
        is_dt_prop = OWL_DATATYPE_PROPERTY in types
        if not (is_class or is_obj_prop or is_dt_prop):
            continue

        subj_curie = curie(subj, ns_to_prefix)
        section = section_for_subject(subj_curie, ontology_text, header_positions, section_cache)

        def first_annotation(preds):
            for pred in preds:
                vals = graph.objects(subj, pred)
                if vals:
                    v = preferred_literal(vals)
                    return v.value if isinstance(v, Literal) else str(v)
            return ""

        def all_annotations(preds):
            for pred in preds:
                vals = graph.objects(subj, pred)
                if vals:
                    return [v.value if isinstance(v, Literal) else str(v) for v in vals]
            return []

        label = first_annotation(LABEL_PREDICATES) or subj_curie
        definition = first_annotation(DEFINITION_PREDICATES)
        example = first_annotation(EXAMPLE_PREDICATES)
        scope_note = all_annotations(SCOPE_NOTE_PREDICATES)

        if is_class:
            named_super, rendered_super = [], []
            for val in graph.objects(subj, OWL_SUBCLASSOF):
                for conjunct in split_class_expression_terms(val, graph):
                    if isinstance(conjunct, IRI):
                        named_super.append(curie(conjunct, ns_to_prefix))
                    else:
                        rendered_super.append(render_class_expression(conjunct, graph, ns_to_prefix))
            super_classes = sorted(set(named_super)) + rendered_super

            disjoint = []
            for val in graph.objects(subj, OWL_DISJOINTWITH):
                disjoint.append(render_class_expression(val, graph, ns_to_prefix))

            eqv_vals = graph.objects(subj, OWL_EQUIVALENTCLASS)
            equivalent_class = None
            if eqv_vals:
                parts = []
                for conjunct in split_class_expression_terms(eqv_vals[0], graph):
                    parts.append(render_class_expression(conjunct, graph, ns_to_prefix))
                equivalent_class = " and ".join(parts) if parts else None

            classes.append({
                "id": subj_curie, "label": label, "definition": definition, "example": example,
                "scopeNote": scope_note, "subClassOf": super_classes,
                "equivalentClass": equivalent_class,
                "disjointWith": sorted(set(disjoint)), "section": section,
            })
        else:
            sub_prop_of = sorted(set(curie(v, ns_to_prefix) for v in graph.objects(subj, RDFS_SUBPROPERTYOF) if isinstance(v, IRI)))
            domain = sorted(set(render_class_expression(v, graph, ns_to_prefix) for v in graph.objects(subj, RDFS_DOMAIN)))
            range_ = sorted(set(render_class_expression(v, graph, ns_to_prefix) for v in graph.objects(subj, RDFS_RANGE)))
            domain_includes = sorted(set(render_class_expression(v, graph, ns_to_prefix) for v in graph.objects(subj, GIST_DOMAININCLUDES)))
            range_includes = sorted(set(render_class_expression(v, graph, ns_to_prefix) for v in graph.objects(subj, GIST_RANGEINCLUDES)))
            entry = {
                "id": subj_curie, "label": label, "definition": definition,
                "subPropertyOf": sub_prop_of, "domain": domain, "range": range_,
                "domainIncludes": domain_includes, "rangeIncludes": range_includes,
                "section": section,
            }
            (obj_props if is_obj_prop else dt_props).append(entry)

    sections_in_order, seen = [], set()
    for entry in classes + obj_props + dt_props:
        if entry["section"] not in seen:
            seen.add(entry["section"])
            sections_in_order.append(entry["section"])

    return classes, obj_props, dt_props, sections_in_order


def get_definition_from_reference(ref_graph, term_iri):
    """Look the term up in a `--ref` vocabulary by its IRI.

    By IRI, not by CURIE, and that is the whole point. This compared the
    CURIE the *reference* file would write against the CURIE the *ontology*
    wrote, so resolution depended on two independent files having chosen the
    same prefix -- and silently found nothing when they had not.

    Which is the common case, not an edge case. A published vocabulary
    typically declares its own namespace twice: once named, once as the
    default. W3C's `org.ttl` has both `@prefix org:` and `@prefix :` for
    `http://www.w3.org/ns/org#`; the prefix map is built by inverting
    {prefix: ns}, so the later declaration wins and every term in it renders
    `:OrganizationalUnit`. The ontology under test calls the same term
    `org:OrganizationalUnit`. The strings differ, the lookup fails, and the
    output reports "5 external terms (0 resolved)" while sitting on a file
    that defines them.

    `extract_external_reuse` above already learned this -- its local-namespace
    test compares namespace URIs precisely because several prefixes can alias
    one namespace, with a comment saying so. The lesson had not reached here.
    An IRI is the term's identity; a prefix is one file's shorthand for it.
    """
    for subj in ref_graph._by_subject:
        if not isinstance(subj, IRI):
            continue
        if str(subj) == str(term_iri):
            types = set(ref_graph.types(subj))
            if OWL_CLASS in types:
                kind = "Class"
            elif OWL_OBJECT_PROPERTY in types:
                kind = "ObjectProperty"
            elif OWL_DATATYPE_PROPERTY in types:
                kind = "DatatypeProperty"
            else:
                kind = "Other"
            for pred in DEFINITION_PREDICATES:
                vals = ref_graph.objects(subj, pred)
                if vals:
                    v = preferred_literal(vals)
                    return {"kind": kind, "definition": v.value if isinstance(v, Literal) else str(v)}
            return {"kind": kind, "definition": ""}
    return None


def resolve_prefix(term, ns_to_prefix):
    """Returns the declared prefix for this IRI's namespace if one
    genuinely matches (longest namespace match wins), else None. Unlike
    curie(), this never falls back to treating an unresolved IRI's own
    'http:' as if it were a prefix - a raw, undeclared-namespace IRI
    (e.g. the ontology's own IRI, or rdf:type when no rdf: prefix
    happens to be declared in a given file) is not vocabulary the
    ontology depends on in the sense external-reuse tracking cares
    about, and treating its scheme as a bogus 'prefix' was a real bug."""
    if not isinstance(term, IRI):
        return None
    best = None
    for ns, prefix in ns_to_prefix.items():
        if term.startswith(ns) and (best is None or len(ns) > len(best[0])):
            best = (ns, prefix)
    return best[1] if best else None


def extract_external_reuse(graph, ns_to_prefix, local_ns, ref_graphs_and_ns):
    external_terms = set()

    def maybe_add(term):
        if not isinstance(term, IRI):
            return
        prefix = resolve_prefix(term, ns_to_prefix)
        if prefix is None:
            return  # unresolved raw IRI - not classifiable vocabulary, skip
        if term.startswith(local_ns):
            return  # local vocabulary - determined by namespace URI membership,
                     # not by comparing curie prefix strings, since multiple
                     # prefixes can alias the same namespace (encountered in
                     # the wild: a real ontology declared both a bare `:` and
                     # a named `veho:` for the identical namespace URI - a
                     # string-based "prefix != local_prefix" check would
                     # wrongly flag the alias not chosen as local_prefix)
        if prefix in STRUCTURAL_PREFIXES:
            return
        c = curie(term, ns_to_prefix)
        if c.endswith(":"):
            return  # e.g. an owl:imports target that is itself exactly a
                     # declared namespace URI with no local name - not a term
        # The CURIE is what the reader sees; the IRI is what the lookup uses.
        # Keeping both is what stops the two files' prefix choices mattering.
        external_terms.add((c, str(term)))

    for s, p, o in graph.triples:
        for term in (s, p, o):
            if isinstance(term, Collection):
                for item in term:
                    maybe_add(item)
            else:
                maybe_add(term)

    reuse = []
    for term_curie, term_iri in sorted(external_terms):
        info = None
        for ref_graph, _ref_ns in ref_graphs_and_ns:
            info = get_definition_from_reference(ref_graph, term_iri)
            if info:
                break
        if info:
            reuse.append({"id": term_curie, "kind": info["kind"], "definition": info["definition"]})
        else:
            reuse.append({"id": term_curie, "kind": "Unknown", "definition": ""})
    return reuse


def count_individuals_per_class(instances_graph, ns_to_prefix):
    if instances_graph is None:
        return {}
    counts = Counter()
    for s, p, o in instances_graph.triples:
        if p == RDF_TYPE and isinstance(o, IRI):
            counts[curie(o, ns_to_prefix)] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def read_normalized(path):
    """Reads `path` (a local file, an http(s) URL, or either gzip-compressed
    -- see `io_utils`) with line endings normalized to `\\n`."""
    return io_utils.read_text(path).replace("\r\n", "\n").replace("\r", "\n")


def build_ns_maps(prefixes):
    ns_to_prefix = dict(prefixes)  # {ns_iri: prefix} - note parser gives {prefix: ns_iri}, invert
    return {v: k for k, v in prefixes.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--instances", default=None)
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--out", default="ontology_doc_data.json")
    args = parser.parse_args(argv)

    ontology_text = read_normalized(args.ontology)
    triples, prefixes = parse_turtle(ontology_text)
    graph = Graph(triples)
    ns_to_prefix = build_ns_maps(prefixes)

    if not prefixes:
        print("ERROR: no @prefix declarations found - is this a valid Turtle file?", file=sys.stderr)
        sys.exit(1)

    namespaces = [{"prefix": p, "uri": str(u)} for p, u in prefixes.items()]

    local_ns, local_prefix = detect_local_namespace(graph, namespaces, explicit_prefix=args.prefix)
    if local_prefix is None:
        print("ERROR: could not auto-detect the ontology's own prefix; pass --prefix explicitly.", file=sys.stderr)
        sys.exit(1)

    classes, obj_props, dt_props, sections = extract(graph, ns_to_prefix, local_ns, local_prefix, ontology_text)
    header = extract_ontology_header(graph, ns_to_prefix)

    ref_graphs_and_ns = []
    for ref_path in args.ref:
        ref_text = read_normalized(ref_path)
        ref_triples, ref_prefixes = parse_turtle(ref_text, source=ref_path)
        ref_graph = Graph(ref_triples)
        ref_ns_to_prefix = build_ns_maps(ref_prefixes)
        ref_graphs_and_ns.append((ref_graph, ref_ns_to_prefix))

    external_reuse = extract_external_reuse(graph, ns_to_prefix, local_ns, ref_graphs_and_ns)
    unresolved = [r["id"] for r in external_reuse if r["kind"] == "Unknown"]
    if unresolved:
        print(f"NOTE: {len(unresolved)} external term(s) referenced with no --ref file resolving a definition:", file=sys.stderr)
        for t in unresolved[:20]:
            print(f"  - {t}", file=sys.stderr)
        if len(unresolved) > 20:
            print(f"  ... and {len(unresolved) - 20} more", file=sys.stderr)

    individual_counts = {}
    if args.instances:
        instances_text = read_normalized(args.instances)
        inst_triples, inst_prefixes = parse_turtle(instances_text)
        instances_graph = Graph(inst_triples)
        inst_ns_to_prefix = build_ns_maps({**prefixes, **inst_prefixes})
        individual_counts = count_individuals_per_class(instances_graph, inst_ns_to_prefix)

    data = {
        "ontology": header,
        "namespaces": namespaces,
        "localPrefix": local_prefix,
        "classes": classes,
        "objectProperties": obj_props,
        "datatypeProperties": dt_props,
        "sections": sections,
        "externalReuse": external_reuse,
        "individualCounts": individual_counts,
    }

    Path(args.out).write_text(json.dumps(data, indent=2))
    print(f"Wrote {args.out}: prefix='{local_prefix}', {len(classes)} classes, "
          f"{len(obj_props)} object properties, {len(dt_props)} datatype properties, "
          f"{len(sections)} sections, {len(external_reuse)} external terms "
          f"({len(external_reuse) - len(unresolved)} resolved).")


if __name__ == "__main__":
    main()
