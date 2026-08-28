"""Command-line interface for the consolidated ontology suite.

Subcommands mirror the pipeline stages in ``pipeline.py``:

    ontology-quality-suite ontology            --ontology domain.ttl
    ontology-quality-suite checks              --ontology domain.ttl [--data data.ttl]
    ontology-quality-suite sketch              --queries queries/ [--ontology domain.ttl]
    ontology-quality-suite triplify            --csv-dir csv/ --queries queries/
    ontology-quality-suite data                data.ttl [more.ttl ...] [--ontology domain.ttl]
    ontology-quality-suite docgen              --ontology domain.ttl [--instances data.ttl] [--ref imported.ttl ...]
    ontology-quality-suite run                 whichever of --ontology/--queries/--csv-dir/--data apply
    ontology-quality-suite version-diff        old.ttl new.ttl
    ontology-quality-suite consistency         --new domain.ttl [--old domain-v1.ttl] [--queries queries/] [--apply-repairs]
    ontology-quality-suite consistency-remote  --query-endpoint URL --manifest graphs.json [--update-endpoint URL]
    ontology-quality-suite pattern-consistency --queries queries/ --ontology domain.ttl --taxonomy taxonomy.ttl

Each subcommand is independently runnable (matching each ported tool's own
original standalone CLI) and writes, under ``--out-dir``: ``report.html``,
``full_results.csv``, per-category/per-check summaries, ``cucumber.json``,
and Gherkin ``features/*.feature`` -- via the same report layer regardless
of which stage(s) produced the underlying findings. ``run`` additionally
merges every applicable stage's findings into one combined report.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import config, io_utils, pipeline
from . import consistency as consistency_api
from . import pattern_consistency
from .checks.merge import ResultRow
from .checks.registry import Registry
from .dataquality import data_quality
from .reasoning.consistency import REASONER_CHOICES
from .remote import fuseki
from .remote import manifest as graph_manifest
from .report.cucumber import write_cucumber_json, write_gherkin_feature_files
from .report.html_report import write_html_report
from .report.plots import write_all_plots
from .report.tables import write_all_tables
from .sketch import prefix_alignment as pa
from .sketch import tarql_visualiser
from .ontologyeval import ontology_evaluation
from .versioning import diff as version_diff

SEVERITY_THRESHOLD = {"Violation": 0, "Warning": 1, "Info": 2}


def _write_reports(
    rows: List[ResultRow],
    registry: Registry,
    out_dir: Path,
    title: str,
    artifacts: Optional[List[Tuple[str, Path]]] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_all_tables(rows, registry, out_dir)
    write_all_plots(rows, out_dir / "plots")
    write_cucumber_json(rows, registry, out_dir / "cucumber.json")
    write_gherkin_feature_files(rows, registry, out_dir / "features")
    write_html_report(rows, registry, out_dir / "plots", out_dir / "report.html", title=title, artifacts=artifacts)


def _print_summary(rows: List[ResultRow], out_dir: Path, warnings: List[str]) -> dict:
    counts = {sev: sum(1 for r in rows if r.severity == sev) for sev in ("Violation", "Warning", "Info")}
    print(f"Findings: {len(rows)} total ({counts['Violation']} Violation, {counts['Warning']} Warning, {counts['Info']} Info)")
    print(f"Reports written to: {out_dir.resolve()}")
    print(f"  - {out_dir / 'report.html'} (start here)")
    print(f"  - {out_dir / 'full_results.csv'}")
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    return counts


def _filter_own_namespace(rows: List[ResultRow], own_namespace: Optional[str]) -> List[ResultRow]:
    """Restrict a finished ResultRow set to findings in the caller's own
    namespace -- a report-layer filter (applied after checking, right before
    writing reports/printing the summary), not a re-scoping of what's
    checked. Findings against imported vocabulary terms (e.g. a domain
    violation on a foaf: property) are still computed correctly -- imports
    stay resolved -- they're just excluded from the shown/written results."""
    if not own_namespace:
        return rows
    return [r for r in rows if r.focus_node.startswith(own_namespace)]


def _exit_code(counts: dict, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = SEVERITY_THRESHOLD[fail_on]
    triggered = any(n > 0 for sev, n in counts.items() if SEVERITY_THRESHOLD[sev] <= threshold)
    return 1 if triggered else 0


def _add_common_reasoning_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--reasoner", default="auto", choices=list(REASONER_CHOICES),
        help="reasoning backend: auto (owlrl + external if available), owlrl-only, hermit, pellet, or none",
    )
    p.add_argument("--fail-on", default="Violation", choices=["Violation", "Warning", "Info", "never"])


def _add_profile_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--profile", action="append", choices=["EL", "QL", "RL"], default=[], dest="profile",
        help="check OWL2 profile membership (repeatable, e.g. --profile EL --profile QL). "
             "Off by default -- the ontology is assumed to be full OWL2 DL, and no profile-violation "
             "findings (REA-010/011/012) are reported unless a profile is explicitly requested here.",
    )


def _add_import_args(p: argparse.ArgumentParser) -> None:
    """Every subcommand that takes --ontology resolves owl:imports the same
    way (local-first, network only if asked) -- shared so behavior can't
    silently drift between them the way it once did (checks/sketch/data
    used to load the ontology file alone, with no import resolution at
    all, even when these flags were passed to `run`)."""
    p.add_argument("--import-dir", default=None, help="searched recursively for local copies of imports (default: the ontology file's own directory)")
    p.add_argument("--exclude-imports", action="store_true", help="evaluate the ontology file alone; owl:imports are neither fetched nor merged")
    p.add_argument("--allow-network", action="store_true", help="fetch an unresolved owl:imports IRI over HTTP (off by default)")


def _add_engine_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--engine", default=pipeline.default_engine(), choices=list(pipeline.ENGINE_CHOICES),
        help="which formulation of the registry-driven suite to run: 'both' runs pyshacl and the "
             "portable SPARQL layer and cross-checks them for drift; 'sparql' runs the portable layer "
             "only -- every check has a SPARQL form, so this finds the same real findings, just "
             "without the drift signal, and is typically much faster (pyshacl spends most of a run's "
             "time on its own Python-level shape traversal on top of the same SPARQL the portable "
             "layer runs directly); 'shacl' runs pyshacl only; 'native' runs the optional native "
             "(Rust) SHACL engine instead of pyshacl (see checks/shacl_native_runner.py -- not a "
             "default dependency; falls back to raising unless installed); 'native+sparql' is the "
             "fast analogue of 'both'. Defaults to 'native+sparql' if the native engine package is "
             "installed, else 'both'.",
    )


def _add_verbose_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="print what each input option actually resolved to before running: which query/data "
             "files --file-pattern matched, which owl:imports resolved (and from where) or failed to, "
             "and which check engine ran",
    )


def _add_own_namespace_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--own-namespace", default=None,
        help="only show findings whose focus node starts with this IRI prefix (repeatable-free; a "
             "single prefix, e.g. 'https://example.org/acme/'). Filters the report, it does not change "
             "what's checked -- imports are still resolved and reasoned over normally, so a finding in "
             "your own terms that depends on an imported vocabulary's declarations is still caught. "
             "Unlike --exclude-imports, this does not introduce undeclared-term noise; it just hides "
             "pre-existing findings that belong to an imported vocabulary rather than your own.",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ontology-quality-suite", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    ont = sub.add_parser("ontology", help="Evaluate the ontology as authored: OntoQA/OQuaRE, expressivity, OWL2 profile, consistency")
    ont.add_argument("--ontology", required=True)
    _add_import_args(ont)
    ont.add_argument("--registry", default=str(config.DEFAULT_REGISTRY_PATH))
    ont.add_argument("--sparql", default=str(config.DEFAULT_SPARQL_DIR))
    ont.add_argument("--out-dir", default="out/ontology")
    _add_profile_arg(ont)
    _add_common_reasoning_args(ont)
    _add_verbose_arg(ont)

    chk = sub.add_parser("checks", help="Run the registry-driven SHACL+SPARQL suite")
    chk.add_argument("--ontology", default=None)
    chk.add_argument("--data", default=None)
    _add_import_args(chk)
    chk.add_argument("--shapes", default=str(config.DEFAULT_SHAPES_DIR))
    chk.add_argument("--sparql", default=str(config.DEFAULT_SPARQL_DIR))
    chk.add_argument("--registry", default=str(config.DEFAULT_REGISTRY_PATH))
    chk.add_argument("--inference", default="none", choices=["none", "rdfs", "owlrl", "both"])
    _add_engine_arg(chk)
    chk.add_argument("--out-dir", default="out/checks")
    chk.add_argument("--fail-on", default="Violation", choices=["Violation", "Warning", "Info", "never"])
    _add_own_namespace_arg(chk)
    _add_verbose_arg(chk)

    skt = sub.add_parser("sketch", help="Sketch the graph shape of a folder of TARQL/oxi-gen CONSTRUCT queries")
    skt.add_argument("--queries", required=True)
    skt.add_argument("--ontology", default=None, help="if given, diff the sketch's classes/properties against the ontology's declarations")
    _add_import_args(skt)
    skt.add_argument("--file-pattern", default=tarql_visualiser.DEFAULT_QUERY_GLOBS)
    skt.add_argument("--registry", default=str(config.DEFAULT_REGISTRY_PATH))
    skt.add_argument("--out-dir", default="out/sketch")
    skt.add_argument("--fail-on", default="never", choices=["Violation", "Warning", "Info", "never"])
    _add_verbose_arg(skt)

    trp = sub.add_parser("triplify", help="Run CSV files through oxi-gen to produce real RDF data")
    trp.add_argument("--csv-dir", required=True)
    trp.add_argument("--queries", required=True)
    trp.add_argument("--oxi-gen-bin", default=None)
    trp.add_argument("--delimiter", default=None)
    trp.add_argument("--tab", action="store_true")
    trp.add_argument("--no-header-row", action="store_true")
    trp.add_argument("--normalize", action="store_true")
    trp.add_argument("--ntriples", action="store_true")
    trp.add_argument("--dedup", type=int, default=None)
    trp.add_argument("--test", type=int, default=None, dest="test_rows")
    trp.add_argument("--out-dir", default="out/data")

    dat = sub.add_parser("data", help="Assess real triplified data, optionally against the ontology it should conform to")
    dat.add_argument("data", nargs="+", help="turtle file(s) and/or folder(s) of them")
    dat.add_argument("--ontology", default=None)
    _add_import_args(dat)
    dat.add_argument("--file-pattern", default=data_quality.DEFAULT_DATA_GLOBS,
                      help=f"comma-separated glob pattern(s) used to find data files when a `data` argument is a "
                           f"folder (default: {data_quality.DEFAULT_DATA_GLOBS})")
    dat.add_argument("--registry", default=str(config.DEFAULT_REGISTRY_PATH))
    dat.add_argument("--sparql", default=str(config.DEFAULT_SPARQL_DIR))
    dat.add_argument("--sample", type=int, default=None, help="cap the reasoning pass to a CBD sample of this many named subjects")
    _add_engine_arg(dat)
    dat.add_argument("--out-dir", default="out/data-eval")
    _add_common_reasoning_args(dat)
    _add_own_namespace_arg(dat)
    _add_verbose_arg(dat)

    doc = sub.add_parser("docgen", help="Generate a human-readable reference documentation page for the ontology (classes, properties, diagrams)")
    doc.add_argument("--ontology", required=True)
    doc.add_argument("--instances", default=None, help="an instance-data file, to show per-class individual counts")
    doc.add_argument("--ref", action="append", default=[], help="an imported ontology, to resolve external-term definitions (repeatable)")
    doc.add_argument("--prefix", default=None, help="the ontology's own prefix, if it can't be auto-detected")
    doc.add_argument("--template", default=str(config.DEFAULT_DOCGEN_TEMPLATE))
    doc.add_argument("--no-class-diagrams", action="store_true",
                      help="skip per-class .dot/.svg/.png/.ttl diagram generation (on by default, local classes only)")
    doc.add_argument("--diagram-imports", action="store_true",
                      help="also generate diagrams for external/imported classes resolved via --ref (off by default)")
    doc.add_argument("--out-dir", default="out/docgen")

    run = sub.add_parser("run", help="Run every applicable stage given the inputs provided, into one combined report")
    run.add_argument("--ontology", default=None)
    _add_import_args(run)
    run.add_argument("--docgen", action="store_true", help="also generate the ontology-documentation.html reference page (requires --ontology)")
    run.add_argument("--instances", default=None, help="docgen: an instance-data file, to show per-class individual counts")
    run.add_argument("--ref", action="append", default=[], help="docgen: an imported ontology, to resolve external-term definitions (repeatable)")
    run.add_argument("--doc-prefix", default=None, help="docgen: the ontology's own prefix, if it can't be auto-detected")
    run.add_argument("--doc-template", default=str(config.DEFAULT_DOCGEN_TEMPLATE), help="docgen: template path")
    run.add_argument("--no-class-diagrams", action="store_true",
                      help="docgen: skip per-class .dot/.svg/.png/.ttl diagram generation")
    run.add_argument("--diagram-imports", action="store_true",
                      help="docgen: also generate diagrams for external/imported classes resolved via --ref")
    run.add_argument("--queries", default=None)
    run.add_argument("--query-pattern", default=tarql_visualiser.DEFAULT_QUERY_GLOBS,
                      help=f"comma-separated glob pattern(s) used to find query files under --queries, if it's a "
                           f"folder (default: {tarql_visualiser.DEFAULT_QUERY_GLOBS})")
    run.add_argument("--csv-dir", default=None)
    run.add_argument("--data", nargs="*", default=None)
    run.add_argument("--data-pattern", default=data_quality.DEFAULT_DATA_GLOBS,
                      help=f"comma-separated glob pattern(s) used to find data files under --data, for any "
                           f"argument that's a folder (default: {data_quality.DEFAULT_DATA_GLOBS})")
    run.add_argument("--registry", default=str(config.DEFAULT_REGISTRY_PATH))
    run.add_argument("--shapes", default=str(config.DEFAULT_SHAPES_DIR))
    run.add_argument("--sparql", default=str(config.DEFAULT_SPARQL_DIR))
    run.add_argument("--oxi-gen-bin", default=None)
    run.add_argument("--sample", type=int, default=None)
    _add_engine_arg(run)
    run.add_argument("--out-dir", default="out")
    _add_profile_arg(run)
    _add_common_reasoning_args(run)
    _add_own_namespace_arg(run)
    _add_verbose_arg(run)

    vdiff = sub.add_parser("version-diff", help="Compare two versions of an ontology and suggest a semver-style bump")
    vdiff.add_argument("old", help="the earlier ontology version")
    vdiff.add_argument("new", help="the later ontology version")
    vdiff.add_argument("--import-dir", default=None, help="applies to both --old and --new; searched recursively (default: each file's own directory)")
    vdiff.add_argument("--exclude-imports", action="store_true", help="diff each file alone; owl:imports are neither fetched nor merged for either version")
    vdiff.add_argument("--allow-network", action="store_true")
    vdiff.add_argument("--out-dir", default="out/version-diff")
    vdiff.add_argument("--json", action="store_true", help="also write diff.json (always writes diff.txt)")
    vdiff.add_argument(
        "--fail-on", default="never", choices=["major", "minor", "patch", "never"],
        help="exit non-zero if the detected bump is at/above this level (default: never)",
    )
    _add_verbose_arg(vdiff)

    cons = sub.add_parser(
        "consistency",
        help="Check ontology-version and TARQL/oxi-gen-vs-ontology consistency together, with suggested repair "
             "diffs. Does NOT check a taxonomy layer -- a query hard-coding a nonexistent controlled-vocabulary "
             "reference reports clean here; see the separate 'pattern-consistency' subcommand for that boundary.",
    )
    cons.add_argument("--new", required=True, help="the ontology version to check against")
    cons.add_argument("--old", default=None, help="an earlier ontology version, to also run version-diff + rename detection")
    cons.add_argument("--queries", action="append", default=[], help="a TARQL/oxi-gen query file or folder (repeatable)")
    cons.add_argument("--ontology", action="append", default=[], dest="ontology_paths",
                       help="an additional ontology file to consider for TARQL alignment (repeatable; --new is "
                            "always included). --new's own owl:imports are already resolved automatically via "
                            "--import-dir/--allow-network below -- this flag is for extra files outside that "
                            "resolution, e.g. a vocabulary not reachable via owl:imports at all")
    cons.add_argument("--file-pattern", default=tarql_visualiser.DEFAULT_QUERY_GLOBS)
    _add_import_args(cons)
    cons.add_argument("--out-dir", default="out/consistency")
    cons.add_argument("--apply-repairs", action="store_true",
                       help="write suggested repairs to their target files in place (default: write .patch files under --out-dir/repairs only)")
    cons.add_argument("--min-confidence", type=float, default=0.5,
                       help="only apply/write repairs at or above this confidence (default: 0.5)")
    cons.add_argument("--fail-on-misalignment", action="store_true",
                       help="exit 1 if any TARQL/ontology misalignment is found (default: always exit 0, report-only)")
    _add_verbose_arg(cons)

    consr = sub.add_parser(
        "consistency-remote",
        help="Run the TARQL/ontology-vs-live-data three-way consistency check against a Fuseki (SPARQL 1.1 Protocol) dataset",
    )
    consr.add_argument("--query-endpoint", required=True, help="the dataset's SPARQL query service URL")
    consr.add_argument("--update-endpoint", default=None, help="the dataset's SPARQL update service URL (only needed to apply repairs remotely)")
    consr.add_argument("--auth-user", default=None)
    consr.add_argument("--auth-password", default=None)
    consr.add_argument("--manifest", required=True, help="a remote.manifest.GraphManifest JSON file binding named graphs to their role/tarql source/ontology graph")
    consr.add_argument("--sample-limit", type=int, default=None, help="cap how many triples are pulled per named graph (see remote.fuseki.load_named_graph)")
    consr.add_argument("--out-dir", default="out/consistency-remote")
    consr.add_argument("--fail-on-misalignment", action="store_true")

    patc = sub.add_parser(
        "pattern-consistency",
        help="Check modelling-pattern consistency across an ontology, a taxonomy, a TARQL/oxi-gen "
             "transformation, and (optionally) real triplified output data",
    )
    patc.add_argument("--queries", action="append", required=True, help="a query file or folder of them (repeatable)")
    patc.add_argument("--ontology", action="append", required=True, dest="ontologies",
                       help="an ontology file (repeatable)")
    patc.add_argument("--taxonomy", action="append", required=True, dest="taxonomies",
                       help="a taxonomy file of controlled-vocabulary individuals (repeatable)")
    patc.add_argument("--output-data", action="append", default=None, dest="output_data",
                       help="real triplified output to also check (repeatable; omit to skip this layer)")
    patc.add_argument("--file-pattern", default=tarql_visualiser.DEFAULT_QUERY_GLOBS,
                       help=f"comma-separated glob pattern(s) for query folders "
                            f"(default: {tarql_visualiser.DEFAULT_QUERY_GLOBS})")
    patc.add_argument("--ignore-prefix", action="append", default=[],
                       help="an additional prefix name to ignore in the ontology<->transformation prefix check "
                            "(repeatable)")
    patc.add_argument("--dot", default=None,
                       help="also write a Graphviz .dot file visualising the transform's CONSTRUCT-template "
                            "shape, coloured red/green/gray by gap/ok/unverified (see "
                            "docs/MODELLING_PATTERN_CONSISTENCY.md) -- render with e.g. "
                            "'dot -Tsvg file.dot -o file.svg'")
    patc.add_argument("--out-dir", default="out/pattern-consistency")
    patc.add_argument("--fail-on-mismatch", action="store_true",
                       help="exit 1 if any modelling-pattern inconsistency is found across ontology, taxonomy, "
                            "transformation, or output data (default: always exit 0, report-only)")
    _add_verbose_arg(patc)

    return p


def cmd_ontology(args) -> int:
    out_dir = Path(args.out_dir)
    registry = Registry.load(args.registry)
    stage = pipeline.run_ontology_stage(
        args.ontology, out_dir,
        import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
        reasoner=args.reasoner, registry=registry, sparql_root=args.sparql, profiles=tuple(args.profile),
    )
    if args.verbose:
        print(pipeline.format_import_report(args.ontology, stage.artifacts["import_report"]))
    artifacts = [("ontology_evaluation.txt", out_dir / "ontology_evaluation.txt"),
                 ("ontology_evaluation.json", out_dir / "ontology_evaluation.json")]
    _write_reports(stage.rows, registry, out_dir, "Ontology Evaluation Report", artifacts)
    counts = _print_summary(stage.rows, out_dir, stage.warnings)
    return _exit_code(counts, args.fail_on)


def cmd_checks(args) -> int:
    out_dir = Path(args.out_dir)
    if not args.ontology and not args.data:
        print("error: 'checks' needs at least one of --ontology or --data", file=sys.stderr)
        return 2
    registry = Registry.load(args.registry)
    stage = pipeline.run_checks_stage(
        registry, out_dir, ontology_path=args.ontology, data_path=args.data,
        shapes_dir=args.shapes, sparql_dir=args.sparql, inference=args.inference, engine=args.engine,
        import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
        verbose=args.verbose,
    )
    rows = _filter_own_namespace(stage.rows, args.own_namespace)
    _write_reports(rows, registry, out_dir, "Registry Checks Report")
    counts = _print_summary(rows, out_dir, stage.warnings)
    return _exit_code(counts, args.fail_on)


def cmd_sketch(args) -> int:
    out_dir = Path(args.out_dir)
    registry = Registry.load(args.registry)
    stage = pipeline.run_sketch_stage(
        args.queries, out_dir, ontology_path=args.ontology, query_pattern=args.file_pattern,
        import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
        verbose=args.verbose,
    )
    artifacts = [
        ("sketch.ttl", stage.artifacts["sketch_path"]),
        ("bind-review.txt", stage.artifacts["bind_report_path"]),
    ]
    _write_reports(stage.rows, registry, out_dir, "TARQL/oxi-gen Sketch Report", artifacts)
    counts = _print_summary(stage.rows, out_dir, stage.warnings)
    gm = stage.artifacts["graph_metrics"]
    print(f"Sketch used {len(stage.artifacts['used_queries'])} query file(s); "
          f"{gm['sizes']['triple_count']} triples, {gm['sizes']['entity_count']} entities.")
    return _exit_code(counts, args.fail_on)


def cmd_triplify(args) -> int:
    out_dir = Path(args.out_dir)
    stage = pipeline.run_triplify_stage(
        args.csv_dir, args.queries, out_dir, oxi_gen_bin=args.oxi_gen_bin,
        delimiter=args.delimiter, tab=args.tab, no_header_row=args.no_header_row,
        normalize=args.normalize, ntriples=args.ntriples, dedup=args.dedup, test_rows=args.test_rows,
    )
    for w in stage.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    output_paths = stage.artifacts.get("output_paths", [])
    print(f"Triplified {len(output_paths)} file(s) into {out_dir.resolve()}")
    for p in output_paths:
        print(f"  - {p}")
    return 1 if not output_paths else 0


def cmd_data(args) -> int:
    out_dir = Path(args.out_dir)
    registry = Registry.load(args.registry)
    stage = pipeline.run_data_stage(
        args.data, out_dir, ontology_path=args.ontology, registry=registry,
        sparql_root=args.sparql, sample=args.sample, reasoner=args.reasoner, engine=args.engine,
        data_pattern=args.file_pattern,
        import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
        verbose=args.verbose,
    )
    rows = _filter_own_namespace(stage.rows, args.own_namespace)
    _write_reports(rows, registry, out_dir, "Data Quality & Conformance Report")
    counts = _print_summary(rows, out_dir, stage.warnings)
    if stage.artifacts.get("sample_note"):
        print(stage.artifacts["sample_note"])
    return _exit_code(counts, args.fail_on)


def cmd_docgen(args) -> int:
    out_dir = Path(args.out_dir)
    stage = pipeline.run_docgen_stage(
        args.ontology, out_dir,
        instances_path=args.instances, ref_paths=args.ref, prefix=args.prefix, template_path=args.template,
        class_diagrams=not args.no_class_diagrams, diagram_imports=args.diagram_imports,
    )
    for w in stage.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"Reference documentation written to: {stage.artifacts['html_path']}")
    if stage.artifacts.get("class_diagrams"):
        print(f"{len(stage.artifacts['class_diagrams'])} class diagram(s) written to: {out_dir / 'class-diagrams'}")
    return 0


def _load_ontology_for_diff(path: str, args):
    if args.exclude_imports:
        graph, report = ontology_evaluation.load_without_imports(path)
    else:
        graph, report = ontology_evaluation.resolve_imports(
            path, args.import_dir, args.allow_network, ontology_evaluation.DEFAULT_IMPORT_GLOBS
        )
    if args.verbose:
        print(pipeline.format_import_report(path, report))
    return graph


def cmd_version_diff(args) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    old_graph = _load_ontology_for_diff(args.old, args)
    new_graph = _load_ontology_for_diff(args.new, args)
    diff, bump = version_diff.diff_ontologies(old_graph, new_graph)

    text = version_diff.format_report(diff, bump, args.old, args.new)
    (out_dir / "diff.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to: {out_dir / 'diff.txt'}")

    if args.json:
        import json
        (out_dir / "diff.json").write_text(json.dumps(version_diff.to_json(diff, bump), indent=2), encoding="utf-8")
        print(f"Written to: {out_dir / 'diff.json'}")

    if args.fail_on == "never":
        return 0
    order = {"none": 0, "patch": 1, "minor": 2, "major": 3}
    threshold = {"patch": 1, "minor": 2, "major": 3}[args.fail_on]
    return 1 if order[bump.value] >= threshold else 0


def cmd_consistency(args) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = consistency_api.check_consistency(
        args.new,
        old_ontology=args.old,
        tarql_sources=args.queries,
        ontology_paths=[args.new] + args.ontology_paths,
        query_pattern=args.file_pattern,
        import_dir=args.import_dir,
        exclude_imports=args.exclude_imports,
        allow_network=args.allow_network,
        verbose=args.verbose,
    )

    text = consistency_api.format_consistency_report(report)
    (out_dir / "consistency.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to: {out_dir / 'consistency.txt'}")

    eligible = [r for r in report.repairs if r.confidence >= args.min_confidence]
    if args.apply_repairs:
        applied = consistency_api.apply_repairs(eligible, min_confidence=args.min_confidence)
        print(f"\nApplied {len(applied)}/{len(report.repairs)} suggested repair(s) directly to their target files "
              f"(confidence >= {args.min_confidence:.0%}).")
    elif report.repairs:
        patch_paths = consistency_api.write_repair_patches(eligible, out_dir / "repairs")
        print(f"\nWrote {len(patch_paths)}/{len(report.repairs)} suggested repair(s) as .patch files under "
              f"{out_dir / 'repairs'} (confidence >= {args.min_confidence:.0%}). Re-run with --apply-repairs to apply them directly.")

    if args.fail_on_misalignment and not report.is_clean:
        return 1
    return 0


def cmd_consistency_remote(args) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    auth = (args.auth_user, args.auth_password) if args.auth_user else None
    dataset = fuseki.FusekiDataset(query_endpoint=args.query_endpoint, update_endpoint=args.update_endpoint, auth=auth)
    manifest = graph_manifest.GraphManifest.load(args.manifest)

    reports = graph_manifest.check_manifest_consistency(dataset, manifest, sample_limit=args.sample_limit)

    all_clean = True
    for report in reports:
        all_clean = all_clean and report.is_clean
        lines = [f"=== {report.graph_uri} ===", f"  source_tarql: {report.source_tarql}", f"  ontology_graph_uri: {report.ontology_graph_uri}"]
        for w in report.warnings:
            lines.append(f"  WARNING: {w}")
        if report.template_vs_ontology is not None:
            lines.append(
                "  template vs ontology: "
                + ("clean" if not report.template_vs_ontology else f"{len(report.template_vs_ontology)} undeclared term(s)")
            )
        if report.live_data_vs_ontology is not None:
            u = report.live_data_vs_ontology
            lines.append(f"  live data vs ontology: {len(u['undeclared_classes_used'])} undeclared class(es), "
                         f"{len(u['undeclared_properties_used'])} undeclared propert(y/ies)")
        if report.template_vs_live_data is not None:
            t = report.template_vs_live_data
            lines.append(f"  template vs live data: {sum(len(v) for v in t.values())} discrepanc(y/ies) " + str(t))
        text = "\n".join(lines)
        print(text)
        safe_name = report.graph_uri.replace("://", "_").replace("/", "_")
        (out_dir / f"{safe_name}.txt").write_text(text, encoding="utf-8")

    print(f"\nReports written to: {out_dir.resolve()}")
    if args.fail_on_misalignment and not all_clean:
        return 1
    return 0


def cmd_pattern_consistency(args) -> int:
    """The taxonomy<->transformation boundary `consistency` doesn't check --
    see `pattern_consistency.py`'s module docstring for why a query
    referencing a nonexistent taxonomy value looks identical, from
    `consistency`'s own checks alone, to a legitimately unverifiable one."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
    report = pattern_consistency.check_four_layer_consistency(
        args.queries, args.ontologies, args.taxonomies,
        output_data_paths=args.output_data,
        query_pattern=args.file_pattern,
        ignore_prefixes=ignore_prefixes,
    )
    text = pattern_consistency.format_four_layer_report(report)
    (out_dir / "pattern-consistency.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to: {out_dir / 'pattern-consistency.txt'}")

    if args.dot:
        dot_path = pattern_consistency.write_consistency_dot(
            args.queries, args.ontologies, args.taxonomies, args.dot,
            query_pattern=args.file_pattern, ignore_prefixes=ignore_prefixes,
        )
        print(f"Wrote {dot_path}")

    if args.fail_on_mismatch and not report.is_clean:
        return 1
    return 0


def cmd_run(args) -> int:
    out_dir = Path(args.out_dir)
    registry = Registry.load(args.registry)
    rows: List[ResultRow] = []
    warnings: List[str] = []
    artifacts: List[Tuple[str, Path]] = []

    if args.ontology:
        stage = pipeline.run_ontology_stage(
            args.ontology, out_dir / "ontology", reasoner=args.reasoner, registry=registry, sparql_root=args.sparql,
            import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
            profiles=tuple(args.profile),
        )
        rows += stage.rows
        warnings += stage.warnings
        artifacts += [("ontology_evaluation.txt", out_dir / "ontology" / "ontology_evaluation.txt")]
        if args.verbose:
            print(pipeline.format_import_report(args.ontology, stage.artifacts["import_report"]))

    if args.docgen:
        if not args.ontology:
            print("WARNING: --docgen requires --ontology; skipping.", file=sys.stderr)
        else:
            docgen_stage = pipeline.run_docgen_stage(
                args.ontology, out_dir / "docgen",
                instances_path=args.instances, ref_paths=args.ref, prefix=args.doc_prefix, template_path=args.doc_template,
                class_diagrams=not args.no_class_diagrams, diagram_imports=args.diagram_imports,
            )
            artifacts += [("ontology-documentation.html", docgen_stage.artifacts["html_path"])]
            warnings += docgen_stage.warnings

    if args.ontology or args.data:
        stage = pipeline.run_checks_stage(
            registry, out_dir / "checks", ontology_path=args.ontology,
            data_path=(args.data[0] if args.data and len(args.data) == 1 else None),
            shapes_dir=args.shapes, sparql_dir=args.sparql, engine=args.engine,
            import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
            verbose=args.verbose,
        )
        rows += stage.rows
        warnings += stage.warnings

    if args.queries:
        stage = pipeline.run_sketch_stage(
            args.queries, out_dir / "sketch", ontology_path=args.ontology, query_pattern=args.query_pattern,
            import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
            verbose=args.verbose,
        )
        rows += stage.rows
        warnings += stage.warnings
        artifacts += [
            ("sketch.ttl", stage.artifacts["sketch_path"]),
            ("bind-review.txt", stage.artifacts["bind_report_path"]),
        ]

    triplified_paths: List[str] = []
    if args.csv_dir and args.queries:
        stage = pipeline.run_triplify_stage(args.csv_dir, args.queries, out_dir / "data", oxi_gen_bin=args.oxi_gen_bin)
        warnings += stage.warnings
        triplified_paths = [str(p) for p in stage.artifacts.get("output_paths", [])]
        for p in triplified_paths:
            artifacts.append((Path(p).name, Path(p)))

    data_paths = list(args.data or []) + triplified_paths
    if data_paths:
        stage = pipeline.run_data_stage(
            data_paths, out_dir / "data-eval", ontology_path=args.ontology, registry=registry,
            sparql_root=args.sparql, sample=args.sample, reasoner=args.reasoner, engine=args.engine,
            data_pattern=args.data_pattern,
            import_dir=args.import_dir, exclude_imports=args.exclude_imports, allow_network=args.allow_network,
            verbose=args.verbose,
        )
        rows += stage.rows
        warnings += stage.warnings

    rows = _filter_own_namespace(rows, args.own_namespace)
    _write_reports(rows, registry, out_dir, "Consolidated Ontology Suite Report", artifacts)
    counts = _print_summary(rows, out_dir, warnings)
    return _exit_code(counts, args.fail_on)


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    handlers = {
        "ontology": cmd_ontology,
        "checks": cmd_checks,
        "sketch": cmd_sketch,
        "triplify": cmd_triplify,
        "data": cmd_data,
        "docgen": cmd_docgen,
        "run": cmd_run,
        "version-diff": cmd_version_diff,
        "consistency": cmd_consistency,
        "consistency-remote": cmd_consistency_remote,
        "pattern-consistency": cmd_pattern_consistency,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
