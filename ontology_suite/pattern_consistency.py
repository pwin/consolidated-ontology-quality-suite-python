"""Cross-layer modelling-pattern consistency.

The same modelling decision has to be repeated consistently across four
places: an **ontology** (OWL2 TBox: classes/properties), a **taxonomy**
(the controlled vocabulary of values data actually uses -- SKOS concepts,
or `gist:Category` individuals reached via `gist:isCategorizedBy`, built
*on* the ontology -- see `docs/UPDATING.md` SS2.2), a **transformation**
(a TARQL/oxi-gen CONSTRUCT query), and real **triple output** (what a
transformation actually produces). A drift at any one layer boundary is a
real, findable bug long before it shows up as bad data -- and each boundary
is small enough to check independently:

  ontology <-> transformation   `sketch.prefix_alignment` (prefix drift +
                                 undeclared classes/properties)
  ontology <-> taxonomy         a taxonomy individual typed with a class
                                 the ontology never declared (reuses
                                 `dataquality.data_quality` directly -- a
                                 taxonomy file is just a small data graph)
  taxonomy <-> transformation   a transform hard-codes a reference to a
                                 controlled-vocabulary individual that
                                 doesn't actually exist in the taxonomy
                                 (`check_taxonomy_references`) -- static,
                                 query-text-only, so it can't see a value
                                 built dynamically per data row (see below)
  ontology+taxonomy <-> output  `dataquality.data_quality` again, against
                                 real triplified output instead of the
                                 static sketch -- *type*-conformance only
  taxonomy <-> output data      `check_taxonomy_membership` -- *identity*
                                 checking against real triplified output:
                                 does every value actually used with a
                                 taxonomy-bound property exist as a real
                                 taxonomy individual? Catches exactly what
                                 the two checks above structurally can't: a
                                 per-row dynamically-constructed reference
                                 (e.g. `BIND(IRI(CONCAT(...,?dept)) AS
                                 ?deptIri)`) has no fixed literal for
                                 `check_taxonomy_references` to inspect, and
                                 an IRI with no `rdf:type` anywhere in the
                                 data being checked is "unverifiable" to
                                 type-conformance checking, not "wrong" --
                                 identical to a reference that genuinely
                                 doesn't exist. Found as a real gap this way
                                 while building an external worked example
                                 against this suite.

`check_four_layer_consistency` runs all four together and returns one
report. See `docs/MODELLING_PATTERN_CONSISTENCY.md` for the worked example
this module was built alongside (`examples/pattern_consistency/`).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from rdflib import RDF, Literal, URIRef

from . import io_utils
from .checks.merge import ResultRow
from .dataquality import data_quality
from .sketch import dot_export, prefix_alignment as pa
from .sketch.tarql_visualiser import DEFAULT_BASE, DEFAULT_QUERY_GLOBS, scratch_namespace


@dataclass
class TaxonomyReferenceGap:
    property: str
    term: str
    detail: str


def check_taxonomy_references(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    taxonomy_paths: Iterable[str | Path],
    *,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
) -> List[TaxonomyReferenceGap]:
    """Find controlled-vocabulary references a TARQL/oxi-gen query
    hard-codes (e.g. `gist:isCategorizedBy ex:Petrol`) that don't resolve
    to an actual individual declared in the given taxonomy set.

    Works from the same CONSTRUCT-template sketch `sketch.prefix_alignment`
    builds: any IRI used as the *object* of a triple that (a) isn't a
    per-row entity built from a CSV-bound `?variable` (recognizable as
    living in the sketch's own scratch namespace), and (b) isn't itself a
    declared class or property, is a literal reference the query author
    wrote directly -- exactly the shape of a hard-coded taxonomy value.
    Everything else about the pattern (which property carries it -- gist's
    `isCategorizedBy`, a SKOS relation, or a domain-specific one) is
    deliberately not hardcoded, since the check works the same way
    regardless of which property links to the controlled vocabulary.
    """
    sketch_graph = pa.build_sketch_graph(tarql_sources, query_pattern)
    ontology_graph = pa.load_merged_ontology_graph(ontology_paths)
    taxonomy_graph = pa.load_merged_ontology_graph(taxonomy_paths)

    declarations = data_quality.ontology_declarations(ontology_graph)
    known_individuals = set(taxonomy_graph.subjects(None, None))
    scratch_ns = scratch_namespace(DEFAULT_BASE)

    findings: List[TaxonomyReferenceGap] = []
    seen = set()
    for _s, p, o in sketch_graph:
        if p == RDF.type or not isinstance(o, URIRef):
            continue
        if str(o).startswith(scratch_ns):
            continue  # a per-row constructed entity, not a hardcoded reference
        if o in declarations["classes"] or o in declarations["properties"]:
            continue
        if o in known_individuals:
            continue
        key = (str(p), str(o))
        if key in seen:
            continue
        seen.add(key)
        findings.append(TaxonomyReferenceGap(
            property=str(p), term=str(o),
            detail=f"{o} is used as the value of {p} in the TARQL query sketch but is not declared "
                   "as an individual anywhere in the given taxonomy set.",
        ))
    return sorted(findings, key=lambda f: (f.property, f.term))


def _infer_taxonomy_properties(ontology_graph, taxonomy_graph) -> Dict[URIRef, URIRef]:
    """Infer which properties point at taxonomy concepts, with no explicit
    configuration needed in the common case: any property whose declared
    `rdfs:range` is a class actually populated (has at least one `rdf:type`
    instance, directly or via a subclass) in `taxonomy_graph` -- e.g.
    `acme:worksIn`'s range is `acme:Department`, and `taxonomy_graph`
    declares `acme:ENG a acme:Department`, so `acme:worksIn` is inferred as
    taxonomy-bound to `acme:Department`.

    Subclass-aware, not just an exact class match: a taxonomy commonly has
    a "category root" range class with real individuals typed one of its
    *subclasses* instead -- this suite's own bundled
    `examples/pattern_consistency/` fixture is exactly this shape
    (`gist:isCategorizedBy`'s range is the generic `gist:Category`;
    `taxonomy.ttl`'s individuals are typed `ex:FuelType`, a
    `rdfs:subClassOf gist:Category`) -- an exact-match-only check would
    silently fail to infer the binding for a fixture as ordinary as this
    suite's own.
    """
    declarations = data_quality.ontology_declarations(ontology_graph)
    parents_of = declarations["parents_of"]
    taxonomy_classes = {o for _, o in taxonomy_graph.subject_objects(RDF.type)}

    def _is_descendant_or_self(cls: URIRef, target: URIRef) -> bool:
        if cls == target:
            return True
        stack, seen = [cls], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            for parent in parents_of.get(current, ()):
                if parent == target:
                    return True
                stack.append(parent)
        return False

    result: Dict[URIRef, URIRef] = {}
    for prop, ranges in declarations["range"].items():
        for range_class in ranges:
            matched = {tc for tc in taxonomy_classes if _is_descendant_or_self(tc, range_class)}
            if matched:
                result[prop] = next(iter(matched))
                break
    return result


def check_taxonomy_membership(
    data_paths: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    taxonomy_paths: Iterable[str | Path],
    *,
    property_to_taxonomy_class: Optional[Dict[URIRef, URIRef]] = None,
) -> List[TaxonomyReferenceGap]:
    """Find a *real, triplified* reference to a taxonomy concept that
    doesn't actually exist -- the identity-based counterpart to
    `check_taxonomy_references` above, for the case that check structurally
    cannot catch: a value built dynamically per data row (e.g.
    `BIND(IRI(CONCAT(...,?department)) AS ?dept)`) has no fixed literal IRI
    in the query text for `check_taxonomy_references` to inspect -- the
    problem only exists once real data is produced. `dataquality.
    data_quality.check_conformance`'s domain/range checking doesn't catch
    it either: an IRI with no `rdf:type` anywhere in the graph being
    checked is "unverifiable" there, not "wrong" (deliberately, so a value
    legitimately typed in a file that wasn't included in a given run isn't
    misreported as broken) -- and a reference to an individual that
    genuinely doesn't exist anywhere looks identical to that case. This
    asks a different, more direct question instead: for a property known
    to point at a taxonomy concept, does every distinct value actually used
    with it in the data appear as a subject anywhere in the taxonomy set?

    `property_to_taxonomy_class` maps a property to the taxonomy class its
    values should belong to (only the keys matter here, not the values --
    membership is checked against the *whole* taxonomy graph, not scoped
    per-class, since a taxonomy file is typically small and single-purpose
    enough that this simpler check is the more useful signal). Left
    unspecified, it's inferred via `_infer_taxonomy_properties` from each
    property's declared `rdfs:range`, when that class is actually populated
    in the given taxonomy set -- covers the common case with no extra
    configuration; pass it explicitly for a property whose range isn't
    declared, or isn't itself the taxonomy's root class.
    """
    data_graph = pa.load_merged_ontology_graph(data_paths)
    ontology_graph = pa.load_merged_ontology_graph(ontology_paths)
    taxonomy_graph = pa.load_merged_ontology_graph(taxonomy_paths)

    if property_to_taxonomy_class is None:
        property_to_taxonomy_class = _infer_taxonomy_properties(ontology_graph, taxonomy_graph)

    known_individuals = set(taxonomy_graph.subjects(None, None))
    findings: List[TaxonomyReferenceGap] = []
    seen = set()
    for prop in property_to_taxonomy_class:
        for _s, o in data_graph.subject_objects(prop):
            if not isinstance(o, URIRef) or o in known_individuals:
                continue
            key = (str(prop), str(o))
            if key in seen:
                continue
            seen.add(key)
            findings.append(TaxonomyReferenceGap(
                property=str(prop), term=str(o),
                detail=f"{o} is used as the value of {prop} in the data graph but is not declared "
                       "as an individual anywhere in the given taxonomy set.",
            ))
    return sorted(findings, key=lambda f: (f.property, f.term))


def check_data_conformance(ontology_paths: Iterable[str | Path], data_paths: Iterable[str | Path], source_label: str) -> List[ResultRow]:
    """Check any RDF data graph (a taxonomy file, or real triplified
    output) against the ontology's declarations, reusing
    `dataquality.data_quality`'s conformance logic directly -- both a
    taxonomy and a triplification's output are, mechanically, just data
    graphs that should conform to the same ontology.

    CNF-005 ("ontology class never populated", Info severity) is dropped
    from the result: it measures population *completeness*, not a
    modelling-pattern *inconsistency* -- a taxonomy file legitimately never
    touches most of the ontology's classes, and including it here would be
    noise for every real example, not a signal. Still available via
    `dataquality.data_quality.check_conformance` directly if you want it.
    """
    ontology_graph = pa.load_merged_ontology_graph(ontology_paths)
    data_graph = pa.load_merged_ontology_graph(data_paths)
    declarations = data_quality.ontology_declarations(ontology_graph)
    conformance = data_quality.check_conformance(declarations, data_graph)
    rows = data_quality.conformance_to_rows(conformance, source_label)
    return [r for r in rows if r.check_id != "CNF-005"]


@dataclass
class FourLayerConsistencyReport:
    ontology_transform: pa.AlignmentReport
    ontology_taxonomy: List[ResultRow]
    taxonomy_transform: List[TaxonomyReferenceGap]
    output_data: Optional[List[ResultRow]] = field(default=None)
    taxonomy_output_data: Optional[List[TaxonomyReferenceGap]] = field(default=None)
    """Identity-based taxonomy<->output-data findings (check_taxonomy_membership)
    -- only populated alongside `output_data`, when `output_data_paths` is
    given. Catches a dynamically-built value (one row-to-row IRI a query
    constructs per CSV row) referencing a taxonomy individual that doesn't
    exist -- exactly the case `taxonomy_transform` structurally can't see,
    since there's no fixed literal in the query text for it to inspect."""

    @property
    def is_clean(self) -> bool:
        return (
            self.ontology_transform.is_clean
            and not self.ontology_taxonomy
            and not self.taxonomy_transform
            and not self.output_data
            and not self.taxonomy_output_data
        )


def check_four_layer_consistency(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    taxonomy_paths: Iterable[str | Path],
    *,
    output_data_paths: Optional[Iterable[str | Path]] = None,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
    ignore_prefixes: Iterable[str] = pa.DEFAULT_IGNORED_PREFIXES,
) -> FourLayerConsistencyReport:
    """Run all four layer-boundary checks together: ontology<->transform,
    ontology<->taxonomy, taxonomy<->transform, and (if `output_data_paths`
    is given -- real triplified output, e.g. from `oxi-gen`)
    ontology<->output-data *and* taxonomy<->output-data (type-conformance
    and taxonomy-membership identity checking respectively -- see
    `check_taxonomy_membership` for why real output needs both)."""
    ontology_transform = pa.check_tarql_ontology_alignment(
        tarql_sources, ontology_paths, query_pattern=query_pattern, ignore_prefixes=ignore_prefixes
    )
    ontology_taxonomy = check_data_conformance(ontology_paths, taxonomy_paths, "taxonomy")
    taxonomy_transform = check_taxonomy_references(
        tarql_sources, ontology_paths, taxonomy_paths, query_pattern=query_pattern
    )
    output_data = (
        check_data_conformance(ontology_paths, output_data_paths, "output-data")
        if output_data_paths is not None
        else None
    )
    taxonomy_output_data = (
        check_taxonomy_membership(output_data_paths, ontology_paths, taxonomy_paths)
        if output_data_paths is not None
        else None
    )
    return FourLayerConsistencyReport(
        ontology_transform=ontology_transform,
        ontology_taxonomy=ontology_taxonomy,
        taxonomy_transform=taxonomy_transform,
        output_data=output_data,
        taxonomy_output_data=taxonomy_output_data,
    )


def format_four_layer_report(report: FourLayerConsistencyReport) -> str:
    if report.is_clean:
        return "No modelling-pattern inconsistencies found across ontology, taxonomy, transformation, or output data."
    lines: List[str] = []

    if not report.ontology_transform.is_clean:
        lines.append("== ontology <-> transformation ==")
        lines.append(pa.format_alignment_report(report.ontology_transform))

    if report.ontology_taxonomy:
        lines.append("== ontology <-> taxonomy ==")
        for row in report.ontology_taxonomy:
            lines.append(f"  [{row.check_id}] {row.message}")

    if report.taxonomy_transform:
        lines.append("== taxonomy <-> transformation ==")
        for f in report.taxonomy_transform:
            lines.append(f"  [undeclared_taxonomy_reference] {f.detail}")

    if report.output_data:
        lines.append("== ontology+taxonomy <-> output data ==")
        for row in report.output_data:
            lines.append(f"  [{row.check_id}] {row.message}")

    if report.taxonomy_output_data:
        lines.append("== taxonomy <-> output data ==")
        for f in report.taxonomy_output_data:
            lines.append(f"  [undeclared_taxonomy_reference] {f.detail}")

    return "\n".join(lines)


def consistency_dot(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    taxonomy_paths: Iterable[str | Path],
    *,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
    ignore_prefixes: Iterable[str] = pa.DEFAULT_IGNORED_PREFIXES,
) -> str:
    """Render the transform's CONSTRUCT-template sketch as a Graphviz DOT
    digraph (via `sketch.dot_export`), coloured using the *same* findings
    `check_four_layer_consistency` reports as text -- red for a triple
    whose predicate or object is a known gap (an undeclared class/property,
    or a taxonomy reference that doesn't exist), dark green for one
    confirmed to resolve against the given ontology/taxonomy declarations,
    and gray for a per-row constructed entity (data the checks don't have
    an opinion on either way). Literal objects are left out of this
    red/green/gray classification entirely -- consistency status was never
    a meaningful thing to say about a literal *value* (only classes/
    properties/taxonomy-references are ever checked), so they fall through
    to `sketch.dot_export`'s own default per-term-kind styling (a blue
    border) instead of being forced gray.

    Only the two *term-level* checks (undeclared classes/properties,
    undeclared taxonomy references) are visualised this way -- a
    `namespace_mismatch` finding is about a query's `PREFIX` declaration,
    not about a specific triple, so it doesn't have a natural place on this
    picture; see the text report for that.
    """
    report = check_four_layer_consistency(
        tarql_sources, ontology_paths, taxonomy_paths,
        query_pattern=query_pattern, ignore_prefixes=ignore_prefixes,
    )
    sketch_graph = pa.build_sketch_graph(tarql_sources, query_pattern)
    ontology_graph = pa.load_merged_ontology_graph(ontology_paths)
    taxonomy_graph = pa.load_merged_ontology_graph(taxonomy_paths)
    declarations = data_quality.ontology_declarations(ontology_graph)
    known_individuals = set(taxonomy_graph.subjects(None, None))
    scratch_ns = scratch_namespace(DEFAULT_BASE)

    gap_terms = {f.term for f in report.taxonomy_transform} | {t.term for t in report.ontology_transform.undeclared_terms}

    def classify(term) -> str:
        key = str(term)
        if key in gap_terms:
            return dot_export.GAP_COLOR
        if term == RDF.type:
            return dot_export.OK_COLOR  # structural, never itself a "declared property" requirement
        if isinstance(term, URIRef) and not key.startswith(scratch_ns):
            if term in declarations["classes"] or term in declarations["properties"] or term in known_individuals:
                return dot_export.OK_COLOR
        return dot_export.NEUTRAL_COLOR

    edge_colors = {}
    node_colors = {}
    for s, p, o in sketch_graph:
        p_color = classify(p)
        o_color = classify(o)
        if dot_export.GAP_COLOR in (p_color, o_color):
            edge_color = dot_export.GAP_COLOR
        elif p_color == dot_export.OK_COLOR and o_color == dot_export.OK_COLOR:
            edge_color = dot_export.OK_COLOR
        else:
            edge_color = dot_export.NEUTRAL_COLOR
        edge_colors[(str(s), str(p), str(o))] = edge_color
        if not isinstance(o, Literal):
            node_colors[str(o)] = o_color
        if not isinstance(s, Literal):
            node_colors[str(s)] = classify(s)

    return dot_export.graph_to_dot(sketch_graph, edge_colors=edge_colors, node_colors=node_colors)


def write_consistency_dot(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    taxonomy_paths: Iterable[str | Path],
    out_path: str | Path,
    **kwargs,
) -> Path:
    out_path = Path(out_path)
    out_path.write_text(consistency_dot(tarql_sources, ontology_paths, taxonomy_paths, **kwargs), encoding="utf-8")
    return out_path


def main(argv):
    parser = argparse.ArgumentParser(
        prog="pattern-consistency",
        description="Check modelling-pattern consistency across an ontology, a taxonomy of controlled-vocabulary "
                     "individuals, a TARQL/oxi-gen transformation, and (optionally) real triplified output data.",
    )
    parser.add_argument("--queries", action="append", required=True, help="a query file or folder of them (repeatable)")
    parser.add_argument("--ontology", action="append", required=True, dest="ontologies",
                         help="an ontology file (repeatable)")
    parser.add_argument("--taxonomy", action="append", required=True, dest="taxonomies",
                         help="a taxonomy file of controlled-vocabulary individuals (repeatable)")
    parser.add_argument("--output-data", action="append", default=None, dest="output_data",
                         help="real triplified output to also check (repeatable; omit to skip this layer)")
    parser.add_argument("--file-pattern", default=DEFAULT_QUERY_GLOBS,
                         help=f"comma-separated glob pattern(s) for query folders (default: {DEFAULT_QUERY_GLOBS})")
    parser.add_argument("--ignore-prefix", action="append", default=[],
                         help="an additional prefix name to ignore in the ontology<->transformation prefix check "
                              "(repeatable)")
    parser.add_argument("--fail-on-mismatch", action="store_true",
                         help="exit 1 if anything is found (default: always exit 0, report only)")
    parser.add_argument("--dot", default=None,
                         help="also write a Graphviz .dot file visualising the transform's CONSTRUCT-template "
                              "shape, coloured red/green/gray by gap/ok/unverified (see "
                              "docs/MODELLING_PATTERN_CONSISTENCY.md) -- render with e.g. "
                              "'dot -Tsvg file.dot -o file.svg'")
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="print which query files --file-pattern actually matched, and which "
                              "--ontology/--taxonomy/--output-data files are being used, before running")
    args = parser.parse_args(argv)

    if args.verbose:
        expanded = io_utils.expand_sources(args.queries, args.file_pattern)
        print(f"[verbose] {args.queries} (--file-pattern {args.file_pattern}): {len(expanded)} query file(s) matched:")
        for p in expanded:
            print(f"    {p}")
        print(f"[verbose] {len(args.ontologies)} ontology file(s): {args.ontologies}")
        print(f"[verbose] {len(args.taxonomies)} taxonomy file(s): {args.taxonomies}")
        if args.output_data:
            print(f"[verbose] {len(args.output_data)} output-data file(s): {args.output_data}")

    ignore_prefixes = pa.DEFAULT_IGNORED_PREFIXES | set(args.ignore_prefix)
    report = check_four_layer_consistency(
        args.queries, args.ontologies, args.taxonomies,
        output_data_paths=args.output_data,
        query_pattern=args.file_pattern,
        ignore_prefixes=ignore_prefixes,
    )
    print(format_four_layer_report(report))

    if args.dot:
        dot_path = write_consistency_dot(
            args.queries, args.ontologies, args.taxonomies, args.dot,
            query_pattern=args.file_pattern, ignore_prefixes=ignore_prefixes,
        )
        print(f"Wrote {dot_path}")

    return 1 if (not report.is_clean and args.fail_on_mismatch) else 0


def run_tool():
    sys.exit(main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"]))


if __name__ == "__main__":
    run_tool()
