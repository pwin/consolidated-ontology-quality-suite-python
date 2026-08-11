"""Validates that TARQL/oxi-gen CONSTRUCT query files stay aligned with the
ontology(ies) they're meant to triplify against, in two independent ways:

1. **Prefix/namespace declarations** (`check_tarql_ontology_prefix_alignment`)
   -- catches drift that reading a single file in isolation can't show:
   - a query rebinds a prefix name the ontology set already uses to a
     *different* namespace IRI -- no syntax error, no runtime error, it
     just triplifies clean-looking data under the wrong vocabulary (e.g. a
     stale IRI left over after a namespace migration, or a typo).
   - a query's namespace IRI *is* one the ontology set declares, just under
     a different prefix label -- harmless to run, but makes cross-
     referencing the query against the ontology by eye easy to get wrong.

2. **Class/property usage** (`check_undeclared_terms`) -- classes (via
   rdf:type) or properties a query's CONSTRUCT template actually builds
   that the given ontology set never declares at all: vocabulary that
   either needs to be added to the ontology, or is a typo/leftover from a
   different one.

`check_tarql_ontology_alignment` runs both together and returns one
combined report.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

import rdflib

from .. import io_utils
from ..dataquality import data_quality
from . import graph_quality, tarql_visualiser
from .tarql_visualiser import DEFAULT_QUERY_GLOBS, extract_prefixes

DEFAULT_IGNORED_PREFIXES = frozenset({"rdf", "rdfs", "owl", "xsd", "xml"})
DEFAULT_ONTOLOGY_GLOBS = "*.ttl,*.owl,*.rdf,*.xml,*.n3,*.nt"


def _expand_paths(sources: Iterable[str | Path], patterns: str) -> List[str]:
    """Flatten a mix of file paths, folders, and http(s) URLs into an
    order-preserving, deduplicated source list (folders globbed with
    `patterns`, a comma-separated list, same convention as
    tarql_visualiser's own -- see `io_utils.expand_sources`, which this
    delegates to)."""
    return io_utils.expand_sources(sources, patterns)


@dataclass
class OntologyNamespaces:
    """Every namespace declaration found across a set of ontology files,
    indexed both ways, each entry keeping the source file it came from so a
    reported misalignment can point somewhere concrete."""
    by_prefix: Dict[str, Dict[str, str]]     # prefix -> {namespace_iri: source_file}
    by_namespace: Dict[str, Dict[str, str]]  # namespace_iri -> {prefix: source_file}


def load_ontology_namespaces(ontology_paths: Iterable[str | Path]) -> OntologyNamespaces:
    """Parse each given ontology file's own namespace declarations (no
    owl:imports resolution -- pass every file you want considered, e.g. the
    main ontology plus each import, explicitly)."""
    by_prefix: Dict[str, Dict[str, str]] = {}
    by_namespace: Dict[str, Dict[str, str]] = {}
    for path in _expand_paths(ontology_paths, DEFAULT_ONTOLOGY_GLOBS):
        graph = rdflib.Graph(bind_namespaces="none")
        io_utils.parse_graph(graph, path)
        for prefix, namespace in graph.namespaces():
            if not prefix:
                continue  # the bare default `:` prefix isn't comparable across files
            iri = str(namespace)
            by_prefix.setdefault(prefix, {}).setdefault(iri, str(path))
            by_namespace.setdefault(iri, {}).setdefault(prefix, str(path))
    return OntologyNamespaces(by_prefix=by_prefix, by_namespace=by_namespace)


@dataclass
class PrefixMisalignment:
    tarql_file: str
    prefix: str
    tarql_namespace: str
    kind: str  # "namespace_mismatch" | "prefix_name_mismatch" | "undeclared_namespace"
    detail: str


def check_tarql_ontology_prefix_alignment(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    *,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
    ignore_prefixes: Iterable[str] = DEFAULT_IGNORED_PREFIXES,
) -> List[PrefixMisalignment]:
    """Iterate every TARQL/oxi-gen CONSTRUCT query file in `tarql_sources`
    (files and/or folders; folders are globbed with `query_pattern`) and
    check each of its declared `PREFIX name: <iri>` lines against the
    namespaces declared across `ontology_paths`, reporting three kinds of
    drift:

    - "namespace_mismatch": the query rebinds a prefix name the ontology
      set already uses, to a *different* namespace IRI -- the one most
      likely to be a real bug.
    - "prefix_name_mismatch": the query's namespace IRI is one the
      ontology set declares, just abbreviated with a different prefix
      label -- harmless to run, but a readability/consistency smell.
    - "undeclared_namespace": neither the prefix name nor the namespace
      IRI appears anywhere in the given ontology set at all -- expected for
      genuinely external vocabulary (the common structural prefixes are
      already filtered via `ignore_prefixes`), otherwise worth checking
      whether the right ontology file was even passed in.

    Findings are returned in file-then-declaration order; an empty list
    means every prefix every query declares matches the ontology set
    exactly.
    """
    ontology_ns = load_ontology_namespaces(ontology_paths)
    ignore = set(ignore_prefixes)
    findings: List[PrefixMisalignment] = []

    for path in _expand_paths(tarql_sources, query_pattern):
        text = io_utils.read_text(path)
        for prefix, iri in extract_prefixes(text).items():
            if prefix in ignore:
                continue

            ontology_iris_for_prefix = ontology_ns.by_prefix.get(prefix)
            if ontology_iris_for_prefix is not None:
                if iri not in ontology_iris_for_prefix:
                    known = ", ".join(f"<{o}> (in {s})" for o, s in ontology_iris_for_prefix.items())
                    findings.append(PrefixMisalignment(
                        tarql_file=str(path), prefix=prefix, tarql_namespace=iri,
                        kind="namespace_mismatch",
                        detail=f"'{prefix}:' is <{iri}> here, but the ontology set binds '{prefix}:' to: {known}",
                    ))
                continue

            owners = ontology_ns.by_namespace.get(iri)
            if owners:
                known = ", ".join(f"'{p}:' (in {s})" for p, s in owners.items())
                findings.append(PrefixMisalignment(
                    tarql_file=str(path), prefix=prefix, tarql_namespace=iri,
                    kind="prefix_name_mismatch",
                    detail=f"<{iri}> is abbreviated '{prefix}:' here, but the ontology set uses: {known}",
                ))
            else:
                findings.append(PrefixMisalignment(
                    tarql_file=str(path), prefix=prefix, tarql_namespace=iri,
                    kind="undeclared_namespace",
                    detail=f"'{prefix}:' (<{iri}>) does not appear, under any prefix, in the given ontology set",
                ))
    return findings


def format_report(findings: List[PrefixMisalignment]) -> str:
    if not findings:
        return "No prefix/namespace misalignments found."
    lines = [f"{len(findings)} prefix/namespace misalignment(s) found:"]
    for f in findings:
        lines.append(f"  [{f.kind}] {f.tarql_file}: {f.detail}")
    return "\n".join(lines)


def load_merged_ontology_graph(ontology_paths: Iterable[str | Path]) -> rdflib.Graph:
    """Merge the given ontology files' full triples into one graph (no
    owl:imports resolution -- pass every file you want considered, e.g. the
    main ontology plus each import, explicitly)."""
    merged = rdflib.Graph(bind_namespaces="none")
    for path in _expand_paths(ontology_paths, DEFAULT_ONTOLOGY_GLOBS):
        io_utils.parse_graph(merged, path)
    return merged


def build_sketch_graph(
    tarql_sources: Iterable[str | Path], query_pattern: str = DEFAULT_QUERY_GLOBS
) -> rdflib.Graph:
    """Build the same in-memory "sketch" graph the `sketch` pipeline stage
    does (tarql_visualiser's CONSTRUCT-template-to-turtle sketch, with the
    namespace-legend triples it adds stripped back out) from the given
    query files -- what the queries would actually build, for comparing
    against the ontology's declarations."""
    paths = _expand_paths(tarql_sources, query_pattern)
    if not paths:
        return rdflib.Graph(bind_namespaces="none")
    graphs = [tarql_visualiser.parse_query(str(p)) for p in paths]
    with tempfile.TemporaryDirectory() as tmp:
        sketch_path = Path(tmp) / "sketch.ttl"
        tarql_visualiser.write_turtle(graphs, str(sketch_path))
        ignored = graph_quality.default_ignored_predicates(
            tarql_visualiser.DEFAULT_BASE,
            tarql_visualiser.DEFAULT_NAMESPACE_PREDICATE,
            tarql_visualiser.DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
        )
        sketch_graph, _ignored_count = graph_quality.load_data_graph(str(sketch_path), ignored)
    return sketch_graph


@dataclass
class UndeclaredTerm:
    kind: str  # "class" | "property"
    term: str
    detail: str


def check_undeclared_terms(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    *,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
) -> List[UndeclaredTerm]:
    """Build the CONSTRUCT-template sketch graph for the given TARQL/oxi-gen
    query files and report every class (used via rdf:type) or property it
    references that isn't declared anywhere in the given ontology set --
    vocabulary the queries assume exists but that still needs to be added
    to the ontology (or is a typo / vocabulary borrowed from elsewhere).

    Reuses `dataquality.data_quality`'s conformance-checking logic directly
    (the same logic the `sketch` pipeline stage's CNF-001/CNF-002 findings
    come from -- a TARQL sketch is just another data graph as far as it's
    concerned) rather than reimplementing rdf:type/property-usage scanning
    here.
    """
    sketch_graph = build_sketch_graph(tarql_sources, query_pattern)
    ontology_graph = load_merged_ontology_graph(ontology_paths)
    declarations = data_quality.ontology_declarations(ontology_graph)
    conformance = data_quality.check_conformance(declarations, sketch_graph)

    findings: List[UndeclaredTerm] = []
    for cls in sorted(conformance["undeclared_classes_used"], key=str):
        findings.append(UndeclaredTerm(
            kind="class", term=str(cls),
            detail=f"{cls} is used with rdf:type in the TARQL query sketch but is never declared "
                   "owl:Class/rdfs:Class in the given ontology set.",
        ))
    for prop in sorted(conformance["undeclared_properties_used"], key=str):
        findings.append(UndeclaredTerm(
            kind="property", term=str(prop),
            detail=f"{prop} is used in the TARQL query sketch but is never declared as a property "
                   "in the given ontology set.",
        ))
    return findings


@dataclass
class AlignmentReport:
    prefix_misalignments: List[PrefixMisalignment]
    undeclared_terms: List[UndeclaredTerm]

    @property
    def is_clean(self) -> bool:
        return not self.prefix_misalignments and not self.undeclared_terms


def check_tarql_ontology_alignment(
    tarql_sources: Iterable[str | Path],
    ontology_paths: Iterable[str | Path],
    *,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
    ignore_prefixes: Iterable[str] = DEFAULT_IGNORED_PREFIXES,
) -> AlignmentReport:
    """Run both alignment checks together against the same TARQL/oxi-gen
    query files and ontology set: declared-prefix/namespace drift
    (`check_tarql_ontology_prefix_alignment`) and undeclared class/property
    usage (`check_undeclared_terms`)."""
    return AlignmentReport(
        prefix_misalignments=check_tarql_ontology_prefix_alignment(
            tarql_sources, ontology_paths, query_pattern=query_pattern, ignore_prefixes=ignore_prefixes
        ),
        undeclared_terms=check_undeclared_terms(tarql_sources, ontology_paths, query_pattern=query_pattern),
    )


def format_alignment_report(report: AlignmentReport) -> str:
    if report.is_clean:
        return "No prefix/namespace misalignments or undeclared classes/properties found."
    lines: List[str] = []
    if report.prefix_misalignments:
        lines.append(f"{len(report.prefix_misalignments)} prefix/namespace misalignment(s):")
        for f in report.prefix_misalignments:
            lines.append(f"  [{f.kind}] {f.tarql_file}: {f.detail}")
    undeclared_classes = [t for t in report.undeclared_terms if t.kind == "class"]
    undeclared_properties = [t for t in report.undeclared_terms if t.kind == "property"]
    if undeclared_classes:
        lines.append(f"{len(undeclared_classes)} class(es) used in TARQL but not declared in the ontology set:")
        for t in undeclared_classes:
            lines.append(f"  [undeclared_class] {t.term}")
    if undeclared_properties:
        lines.append(
            f"{len(undeclared_properties)} propert(y/ies) used in TARQL but not declared in the ontology set:"
        )
        for t in undeclared_properties:
            lines.append(f"  [undeclared_property] {t.term}")
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(
        prog="tarql-prefix-alignment",
        description="Check that TARQL/oxi-gen CONSTRUCT query files use the same prefixes/namespaces "
                     "as a given set of ontology files.",
    )
    parser.add_argument("--queries", action="append", required=True,
                         help="a query file or folder of them (repeatable)")
    parser.add_argument("--ontology", action="append", required=True, dest="ontologies",
                         help="an ontology file (repeatable -- pass every file you want considered)")
    parser.add_argument("--file-pattern", default=DEFAULT_QUERY_GLOBS,
                         help=f"comma-separated glob pattern(s) for query folders (default: {DEFAULT_QUERY_GLOBS})")
    parser.add_argument("--ignore-prefix", action="append", default=[],
                         help="an additional prefix name to ignore, on top of the structural-vocabulary defaults "
                              f"({', '.join(sorted(DEFAULT_IGNORED_PREFIXES))}) (repeatable)")
    parser.add_argument("--fail-on-mismatch", action="store_true",
                         help="exit 1 if any misalignment or undeclared term is found "
                              "(default: always exit 0, report only)")
    args = parser.parse_args(argv)

    report = check_tarql_ontology_alignment(
        args.queries, args.ontologies,
        query_pattern=args.file_pattern,
        ignore_prefixes=DEFAULT_IGNORED_PREFIXES | set(args.ignore_prefix),
    )
    print(format_alignment_report(report))
    return 1 if (not report.is_clean and args.fail_on_mismatch) else 0


def run_tool():
    sys.exit(main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"]))


if __name__ == "__main__":
    run_tool()
