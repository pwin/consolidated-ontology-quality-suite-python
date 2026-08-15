"""Orchestrates the suite's pipeline stages and merges every stage's
findings into one unified ``ResultRow`` list for the report layer.

Each stage is independently callable (mirroring the standalone CLIs each
ported tool already had) and also composable through ``cli.py``'s ``run``
subcommand, which runs whichever stages apply given the inputs provided.
Stage functions call into the ported tools' own pure functions directly
(e.g. ``ontology_evaluation.collect_schema``) for anything the unified
report needs structured data from, and capture each tool's own
``main(argv)`` text/JSON output verbatim into per-stage files under the
run's ``--out-dir`` for parity with the tool's original standalone behavior.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from rdflib import Graph

from . import config
from .checks.literal_typing import SOURCE_LABEL as LITERAL_TYPING_SOURCE
from .checks.literal_typing import run_literal_typing_check
from .checks.merge import ResultRow, build_unified_results
from .checks.registry import Registry
from .checks.runner import load_graph
from .checks.shacl_native_runner import run_shacl_native
from .checks.shacl_native_runner import available as native_shacl_available
from .checks.shacl_runner import load_shapes_graph, run_shacl
from .checks.sparql_runner import run_sparql_checks
from .dataquality import data_quality
from .docgen import build_documentation, extract_ontology_data
from .docgen import class_diagrams as class_diagrams_module
from .ontologyeval import ontology_evaluation
from .reasoning import consistency, profile
from .reasoning.sampling import sample_graph
from .sketch import graph_quality, ontology_quality, prefix_alignment, tarql_visualiser
from .triplify import discovery, oxigen


@dataclass
class StageResult:
    name: str
    rows: List[ResultRow] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _capture_stdout(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


ENGINE_CHOICES = ("both", "sparql", "shacl", "native", "native+sparql")


def default_engine() -> str:
    """``"native+sparql"`` when the optional native engine package is
    installed, else ``"both"`` -- the CLI's own ``--engine`` default
    (`cli.py::_add_engine_arg`), so anyone with the wheel installed gets the
    ~600x-faster path with no flag needed, and anyone without it keeps
    today's pyshacl-based behavior unchanged. Verified exact-parity findings
    between the two (`tests/test_shacl_native_runner.py`), so this changes
    wall-clock time, not results.

    Deliberately *not* used as the default for `run_registry_suite_on_graph`/
    `run_checks_stage`/`run_data_stage` themselves: those keep a literal
    `"both"` default so library callers get deterministic, environment-
    independent behavior -- `tests/test_vehicle_gist_checks.py` in particular
    pins an exact finding count against pyshacl specifically and calls
    `run_registry_suite_on_graph` without passing `engine`.
    """
    return "native+sparql" if native_shacl_available() else "both"


def run_registry_suite_on_graph(
    working_graph: Graph,
    registry: Registry,
    shapes_dir: str | Path = config.DEFAULT_SHAPES_DIR,
    sparql_dir: str | Path = config.DEFAULT_SPARQL_DIR,
    inference: str = "none",
    engine: str = "both",
) -> List[ResultRow]:
    """Run the registry-driven SHACL+SPARQL suite over an already-loaded,
    in-memory graph. Shared by ``run_checks_stage`` (which loads the graph
    from file paths first) and ``run_data_stage`` (which already has the
    aggregate data(+ontology) graph in memory).

    ``engine`` picks which of the two cross-validated formulations to run:

    - ``"both"`` (default) -- pyshacl *and* the portable SPARQL layer, as
      originally designed, so a check that only fires from one engine and
      not the other is a visible signal that the two formulations have
      drifted apart (see docs/EXTENDING.md).
    - ``"sparql"`` -- portable SPARQL only. Every check this suite has is
      implemented in SPARQL (`sparql/**/*.rq`); only a subset (18 of 50, at
      last count) *also* has a SHACL twin, and there is currently no check
      implemented in SHACL alone. Running SPARQL-only therefore finds the
      exact same set of real findings, just without the cross-validation
      signal -- and roughly 8x faster: pyshacl spends the overwhelming
      majority of a run's wall-clock time on its own Python-level shape
      traversal and constraint dispatch, on top of the same SPARQL
      execution the portable layer already does directly (measured: ~193s
      pyshacl vs. ~27s portable SPARQL for the same ~50-check pass over a
      real ~3,300-triple ontology).
    - ``"shacl"`` -- pyshacl only, for symmetry/completeness; not
      recommended given the above (strictly slower, no broader coverage).
    - ``"native"`` -- the native (Rust) SHACL engine only
      (`checks/shacl_native_runner.py`), instead of pyshacl. Verified to
      find the exact same findings pyshacl does on this suite's own shapes
      (see `tests/test_shacl_native_runner.py`); requires the optional
      `shacl` package (see that module's docstring -- not on PyPI yet).
      `inference` may be `"none"` or `"rdfs"` under this engine -- the
      native engine's own supported subset (no OWL2-RL reasoner); `"owlrl"`
      or `"both"` raise `ValueError` here rather than silently downgrading.
    - ``"native+sparql"`` -- the native engine *and* the portable SPARQL
      layer, i.e. the fast analogue of `"both"`: the same cross-validation
      drift-detection signal, without pyshacl's cost.

    If a future check is ever added in SHACL only (no `.rq` twin),
    ``"sparql"`` would silently miss it -- `tests/test_check_coverage.py`
    guards against that by asserting the SHACL-only set stays empty.
    """
    uses_pyshacl = engine in ("both", "shacl")
    uses_native = engine in ("native", "native+sparql")
    shapes_graph = load_shapes_graph(shapes_dir) if (uses_pyshacl or uses_native) else Graph()

    shacl_results = Graph()
    if uses_pyshacl:
        _conforms, shacl_results, _text = run_shacl(working_graph, shapes_graph, inference=inference)
    elif uses_native:
        if not native_shacl_available():
            raise RuntimeError(
                f"--engine {engine} needs the optional `shacl` native engine package -- "
                "see checks/shacl_native_runner.py's module docstring"
            )
        if inference not in ("none", "rdfs"):
            raise ValueError(
                f"--engine {engine} only supports --inference none/rdfs (got {inference!r}); "
                "the native engine has no OWL2-RL reasoner"
            )
        _conforms, shacl_results, _text = run_shacl_native(working_graph, shapes_graph, inference=inference)

    sparql_results = Graph()
    if engine in ("both", "sparql", "native+sparql"):
        sparql_results, _outcomes = run_sparql_checks(working_graph, sparql_dir)

    # Runs under every engine, deliberately: it covers a blind spot of the
    # *check formulations* (a regex over a lexical form rdflib may already
    # have rewritten -- see checks/literal_typing.py), not of one engine, so
    # excluding it from any one engine would reintroduce exactly the kind of
    # engine-dependent finding set --engine is not supposed to change.
    extra_results = [(run_literal_typing_check(working_graph), LITERAL_TYPING_SOURCE)]
    return build_unified_results(
        shacl_results, sparql_results, registry, shapes_graph, extra_results=extra_results
    )


def format_import_report(path: str | Path, report: dict) -> str:
    """Renders an ``ontology_evaluation.resolve_imports``/``load_without_imports``
    report as human-readable text -- shared by every ``--verbose`` print site
    so the same information reads identically everywhere it's shown."""
    lines = [f"[verbose] {path}:"]
    if report["excluded"]:
        lines.append(f"  owl:imports present but excluded (--exclude-imports): {', '.join(report['excluded'])}")
    if report["resolved"]:
        lines.append(f"  {len(report['resolved'])} owl:imports resolved:")
        for r in report["resolved"]:
            lines.append(f"    {r['iri']}  <-  {r['source']}")
    if report["unresolved"]:
        lines.append(
            f"  {len(report['unresolved'])} owl:imports UNRESOLVED "
            f"(--allow-network={report['network_allowed']}):"
        )
        for iri in report["unresolved"]:
            lines.append(f"    {iri}")
    if not report["excluded"] and not report["resolved"] and not report["unresolved"]:
        lines.append("  no owl:imports found")
    return "\n".join(lines)


def load_ontology_graph(
    ontology_path: str | Path,
    *,
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    verbose: bool = False,
) -> Graph:
    """Load an ontology file, transitively resolving its ``owl:imports`` the
    same way the ``ontology`` stage and ``version-diff`` already do (via
    ``ontology_evaluation.resolve_imports``/``load_without_imports``) --
    shared so every stage that takes ``--ontology`` behaves consistently.

    Before this existed, only ``run_ontology_stage`` actually resolved
    imports; ``run_checks_stage``, ``run_sketch_stage``, and
    ``run_data_stage`` each loaded the ontology file alone via a plain
    parse. Caught as a real bug from actual user-reported output: any
    ontology that imports another one by its own ``owl:versionIRI`` (gist's
    own convention, and this suite's own `examples/vehicle/` fixture) would
    have every imported term come back "undefined" for the `checks`/
    `sketch`/`data` stages specifically -- 358 findings, almost entirely
    false positives, even though `--import-dir`/`--allow-network` were
    passed on the command line, because `run`'s own call into the `checks`
    stage silently dropped them.
    """
    if exclude_imports:
        graph, report = ontology_evaluation.load_without_imports(ontology_path)
    else:
        graph, report = ontology_evaluation.resolve_imports(
            ontology_path, import_dir, allow_network, ontology_evaluation.DEFAULT_IMPORT_GLOBS
        )
    if verbose:
        print(format_import_report(ontology_path, report))
    return graph


# --------------------------------------------------------------------------
# Stage 1: ontology -- the ontology as authored (OntoQA/OQuaRE, expressivity,
# lint) plus OWL2 profile membership and reasoner-backed consistency.
# --------------------------------------------------------------------------
def run_ontology_stage(
    ontology_path: str | Path,
    out_dir: str | Path,
    *,
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    reasoner: str = "auto",
    registry: Optional[Registry] = None,
    sparql_root: str | Path = config.DEFAULT_SPARQL_DIR,
    profiles: tuple = (),
) -> StageResult:
    out_dir = Path(out_dir)
    ontology_path = Path(ontology_path)

    argv = [str(ontology_path)]
    if import_dir:
        argv += ["--import-dir", str(import_dir)]
    if exclude_imports:
        argv += ["--exclude-imports"]
    if allow_network:
        argv += ["--allow-network"]

    _write_text(out_dir / "ontology_evaluation.txt", _capture_stdout(ontology_evaluation.main, argv))
    _write_text(out_dir / "ontology_evaluation.json", _capture_stdout(ontology_evaluation.main, argv + ["--json"]))

    if exclude_imports:
        graph, import_report = ontology_evaluation.load_without_imports(ontology_path)
    else:
        graph, import_report = ontology_evaluation.resolve_imports(
            ontology_path, import_dir, allow_network, ontology_evaluation.DEFAULT_IMPORT_GLOBS
        )

    schema = ontology_evaluation.collect_schema(graph)
    metrics = ontology_evaluation.compute_metrics(schema)

    profile_report = profile.check_profiles(graph, profiles=profiles)
    rows = profile.profile_report_to_rows(profile_report)

    if registry is not None and reasoner != "none":
        rows += consistency.run_consistency_checks(graph, registry, sparql_root, reasoner=reasoner)

    return StageResult(
        name="ontology",
        rows=rows,
        artifacts={
            "graph": graph,
            "import_report": import_report,
            "metrics": metrics,
            "profile_report": profile_report,
        },
    )


# --------------------------------------------------------------------------
# Stage 1b: docgen -- a human-readable reference documentation page for the
# ontology (class/property tables, Mermaid diagrams, external-vocabulary
# cross-references), ported from the standalone `docgen3` project. This is
# reference documentation, not a quality assessment -- it produces no
# ResultRow findings, only an HTML artifact.
# --------------------------------------------------------------------------
def run_docgen_stage(
    ontology_path: str | Path,
    out_dir: str | Path,
    *,
    instances_path: Optional[str | Path] = None,
    ref_paths: Optional[List[str | Path]] = None,
    prefix: Optional[str] = None,
    template_path: str | Path = config.DEFAULT_DOCGEN_TEMPLATE,
    class_diagrams: bool = True,
    diagram_imports: bool = False,
) -> StageResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "ontology_doc_data.json"
    html_path = out_dir / "ontology-documentation.html"

    extract_argv = ["--ontology", str(ontology_path), "--out", str(data_path)]
    if instances_path:
        extract_argv += ["--instances", str(instances_path)]
    for ref in ref_paths or []:
        extract_argv += ["--ref", str(ref)]
    if prefix:
        extract_argv += ["--prefix", prefix]

    extract_ontology_data.main(extract_argv)

    warnings: List[str] = []
    diagram_dir = out_dir / "class-diagrams"
    generated_diagrams = {}
    if class_diagrams:
        doc_data = json.loads(data_path.read_text(encoding="utf-8"))
        diagram_source_paths = [ontology_path] + (list(ref_paths or []) if diagram_imports else [])
        diagram_graph = prefix_alignment.load_merged_ontology_graph(diagram_source_paths)
        render_images = shutil.which("dot") is not None
        if not render_images:
            warnings.append(
                "class diagrams: `dot` (Graphviz) not found on PATH -- writing .dot/.ttl only, skipping .svg/.png"
            )
        generated_diagrams = class_diagrams_module.generate_class_diagrams(
            diagram_graph, doc_data, diagram_dir,
            include_external=diagram_imports, render_images=render_images,
        )
        class_diagrams_module.patch_doc_data_with_diagrams(doc_data, generated_diagrams, out_dir)
        data_path.write_text(json.dumps(doc_data, indent=2))

    build_documentation.main(["--data", str(data_path), "--template", str(template_path), "--out", str(html_path)])

    return StageResult(
        name="docgen",
        artifacts={"data_path": data_path, "html_path": html_path, "class_diagrams": generated_diagrams},
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Stage 2: checks -- the registry-driven SHACL+SPARQL suite.
# --------------------------------------------------------------------------
def run_checks_stage(
    registry: Registry,
    out_dir: str | Path,
    *,
    ontology_path: Optional[str | Path] = None,
    data_path: Optional[str | Path] = None,
    shapes_dir: str | Path = config.DEFAULT_SHAPES_DIR,
    sparql_dir: str | Path = config.DEFAULT_SPARQL_DIR,
    inference: str = "none",
    engine: str = "both",
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    verbose: bool = False,
) -> StageResult:
    if not ontology_path and not data_path:
        raise ValueError("run_checks_stage needs at least one of ontology_path or data_path")

    working_graph = Graph()
    if ontology_path:
        ontology_graph = load_ontology_graph(
            ontology_path, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network,
            verbose=verbose,
        )
        for triple in ontology_graph:
            working_graph.add(triple)
    if data_path:
        if verbose:
            print(f"[verbose] {data_path}: loaded as data")
        for triple in load_graph(data_path):
            working_graph.add(triple)

    if verbose:
        print(f"[verbose] engine: {engine}")
    rows = run_registry_suite_on_graph(working_graph, registry, shapes_dir, sparql_dir, inference, engine=engine)

    return StageResult(name="checks", rows=rows, artifacts={"graph": working_graph})


# --------------------------------------------------------------------------
# Stage 3: sketch -- TARQL/oxi-gen CONSTRUCT-query graph-shape sketch, its
# own OQuaRE/OntoQA metrics, and (if an ontology is given) a conformance
# diff against the ontology's actual declarations.
# --------------------------------------------------------------------------
def run_sketch_stage(
    queries_dir: str | Path,
    out_dir: str | Path,
    *,
    ontology_path: Optional[str | Path] = None,
    query_pattern: str = tarql_visualiser.DEFAULT_QUERY_GLOBS,
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    verbose: bool = False,
) -> StageResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sketch_path = out_dir / "sketch.ttl"
    used_queries = tarql_visualiser.visualise_folder(str(queries_dir), str(sketch_path), patterns=query_pattern)
    if verbose:
        print(f"[verbose] {queries_dir} (--file-pattern {query_pattern}): {len(used_queries)} query file(s) matched:")
        for p in used_queries:
            print(f"    {p}")

    ignored = graph_quality.default_ignored_predicates(
        tarql_visualiser.DEFAULT_BASE,
        tarql_visualiser.DEFAULT_NAMESPACE_PREDICATE,
        tarql_visualiser.DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    )
    sketch_graph, ignored_count = graph_quality.load_data_graph(str(sketch_path), ignored)

    graph_metrics = graph_quality.compute_metrics(sketch_graph)
    schema = ontology_quality.induce_schema(sketch_graph)
    schema_metrics = ontology_quality.compute_metrics(schema)

    rows: List[ResultRow] = []
    if ontology_path:
        ontology_graph = load_ontology_graph(
            ontology_path, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network,
            verbose=verbose,
        )
        declarations = data_quality.ontology_declarations(ontology_graph)
        conformance = data_quality.check_conformance(declarations, sketch_graph)
        rows = data_quality.conformance_to_rows(conformance, "sketch")

    return StageResult(
        name="sketch",
        rows=rows,
        artifacts={
            "sketch_path": sketch_path,
            "used_queries": used_queries,
            "ignored_legend_triples": ignored_count,
            "graph_metrics": graph_metrics,
            "schema_metrics": schema_metrics,
        },
    )


# --------------------------------------------------------------------------
# Stage 4: triplify -- run real CSV data through oxi-gen.
# --------------------------------------------------------------------------
def run_triplify_stage(
    csv_dir: str | Path,
    queries_dir: str | Path,
    out_dir: str | Path,
    *,
    oxi_gen_bin: Optional[str | Path] = None,
    **oxi_gen_kwargs,
) -> StageResult:
    out_dir = Path(out_dir)
    binary = config.find_oxi_gen_binary(oxi_gen_bin)
    if binary is None:
        return StageResult(
            name="triplify",
            warnings=[
                "oxi-gen binary not found. Build it with `cargo build --release` in the sibling "
                f"{config.DEFAULT_OXI_GEN_REPO} checkout, or pass --oxi-gen-bin."
            ],
        )

    jobs, discovery_warnings = discovery.discover_jobs_verbose(csv_dir, queries_dir)
    results, errors = oxigen.run_oxi_gen_batch(binary, jobs, out_dir, **oxi_gen_kwargs)

    warnings = list(discovery_warnings) + [str(e) for e in errors]

    return StageResult(
        name="triplify",
        artifacts={
            "binary": binary,
            "jobs": jobs,
            "results": results,
            "output_paths": [r.output_path for r in results],
        },
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Stage 5: data -- real triplified data vs the ontology, registry checks,
# and (optionally sampled) reasoning-backed consistency checking.
# --------------------------------------------------------------------------
def run_data_stage(
    data_paths: List[str | Path],
    out_dir: str | Path,
    *,
    ontology_path: Optional[str | Path] = None,
    registry: Optional[Registry] = None,
    sparql_root: str | Path = config.DEFAULT_SPARQL_DIR,
    sample: Optional[int] = None,
    reasoner: str = "auto",
    data_pattern: str = data_quality.DEFAULT_DATA_GLOBS,
    engine: str = "both",
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    verbose: bool = False,
) -> StageResult:
    ignored = graph_quality.default_ignored_predicates(
        tarql_visualiser.DEFAULT_BASE,
        tarql_visualiser.DEFAULT_NAMESPACE_PREDICATE,
        tarql_visualiser.DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    )
    resolved = data_quality.resolve_input_paths([str(p) for p in data_paths], data_pattern)
    aggregate_graph, per_file = data_quality.load_data_graphs(resolved, ignored)
    if verbose:
        print(f"[verbose] {len(resolved)} data file(s) matched:")
        for f in per_file:
            print(f"    {f['path']}  ({len(f['graph'])} triples, {f['ignored_triple_count']} ignored)")

    ontology_graph: Optional[Graph] = None
    if ontology_path:
        ontology_graph = load_ontology_graph(
            ontology_path, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network,
            verbose=verbose,
        )

    rows: List[ResultRow] = []
    if ontology_graph is not None:
        declarations = data_quality.ontology_declarations(ontology_graph)
        conformance = data_quality.check_conformance(declarations, aggregate_graph)
        rows += data_quality.conformance_to_rows(conformance, "data")

    if registry is not None:
        working_graph = Graph()
        for triple in aggregate_graph:
            working_graph.add(triple)
        if ontology_graph is not None:
            for triple in ontology_graph:
                working_graph.add(triple)
        if verbose:
            print(f"[verbose] engine: {engine}")
        rows += run_registry_suite_on_graph(working_graph, registry, config.DEFAULT_SHAPES_DIR, sparql_root, engine=engine)

    sample_note = None
    reasoning_graph = aggregate_graph
    if sample is not None:
        reasoning_graph = sample_graph(aggregate_graph, sample)
        sample_note = (
            f"reasoning pass sampled {len(set(reasoning_graph.subjects()))} named subjects "
            f"(of {len(set(aggregate_graph.subjects()))} total) via a Concise Bounded Description each"
        )

    if registry is not None and reasoner != "none":
        graph_for_reasoning = Graph()
        for triple in reasoning_graph:
            graph_for_reasoning.add(triple)
        if ontology_graph is not None:
            for triple in ontology_graph:
                graph_for_reasoning.add(triple)
        rows += consistency.run_consistency_checks(graph_for_reasoning, registry, sparql_root, reasoner=reasoner)

    graph_metrics = graph_quality.compute_metrics(aggregate_graph)

    return StageResult(
        name="data",
        rows=rows,
        artifacts={
            "per_file": per_file,
            "aggregate_graph": aggregate_graph,
            "graph_metrics": graph_metrics,
            "sample_note": sample_note,
        },
    )
