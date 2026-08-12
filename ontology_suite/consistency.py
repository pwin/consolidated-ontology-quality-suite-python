"""Top-level convenience API: given one or more ontology files (optionally
an old/new pair) and one or more TARQL/oxi-gen transformation files, run
every consistency check this suite has for that combination and return one
report, including suggested repair diffs -- the entry point most callers
of this package actually want, tying together:

  - ``versioning.diff`` / ``versioning.rename_detection`` -- has the
    ontology changed between two versions, and how (semver-style bump plus
    detected renames)?
  - ``sketch.prefix_alignment`` -- do the given TARQL/oxi-gen queries still
    use the right namespaces and only reference classes/properties the
    ontology actually declares?
  - ``repair.tarql_repair`` / ``checks.repair`` -- concrete, appliable
    diffs for whatever the above two find, using the detected renames (if
    any) to turn "undeclared term" findings into precise substitutions
    rather than generic "declare this as new" stubs.

For live Fuseki-hosted ontologies/data instead of local files, see
``remote.fuseki`` and ``remote.manifest`` -- ``check_consistency`` below is
the local-file entry point.
"""
from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from . import io_utils
from .ontologyeval import ontology_evaluation
from .pipeline import load_ontology_graph
from .repair import tarql_repair
from .repair.types import RepairSuggestion, apply_suggestion
from .sketch import prefix_alignment as pa
from .sketch.tarql_visualiser import DEFAULT_QUERY_GLOBS
from .versioning import diff as version_diff
from .versioning import rename_detection
from .versioning.diff import BumpLevel, OntologyDiff
from .versioning.rename_detection import TermRename


@dataclass
class ConsistencyReport:
    new_ontology: str
    old_ontology: Optional[str] = None
    ontology_diff: Optional[OntologyDiff] = None
    bump: Optional[BumpLevel] = None
    renames: List[TermRename] = field(default_factory=list)
    alignment: Optional[pa.AlignmentReport] = None
    repairs: List[RepairSuggestion] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True if there's nothing to fix on the TARQL/oxi-gen alignment
        side. A non-NONE version bump is informational, not a problem in
        itself -- it doesn't affect this property."""
        return self.alignment is None or self.alignment.is_clean


@contextlib.contextmanager
def _resolved_imports_tempfile(
    main_ontology: str | Path,
    import_dir: Optional[str | Path],
    exclude_imports: bool,
    allow_network: bool,
):
    """Yields one extra ontology file path covering `main_ontology`'s
    resolved ``owl:imports`` (or ``None`` if `exclude_imports` or nothing
    resolved) -- so the TARQL-alignment half of `check_consistency` sees the
    same `--import-dir`/`--allow-network`-resolved imports the version-diff/
    rename-detection half above already does via `load_ontology_graph`.

    Without this, `pa.check_tarql_ontology_alignment` -> `load_merged_ontology_graph`
    does a plain multi-file parse of exactly the paths it's given, with no
    import resolution at all (that function's own docstring says so) --
    every imported class/property came back "undeclared" unless the caller
    passed each import's local file again via a repeatable `--ontology
    <path>`, even though `--import-dir` looked like it should already cover
    this, since it does for every other `--ontology`-taking stage.

    Reuses `ontology_evaluation.resolve_imports` (the same traversal
    `load_ontology_graph` calls) rather than reimplementing import
    resolution here, and serializes the *whole* resolved merge (main
    ontology + every transitively resolved import, network-fetched ones
    included) to one temp Turtle file, since `load_merged_ontology_graph`
    only accepts file paths, not pre-built graphs -- it's shared with every
    other `--ontology`-taking call site in this suite, so it isn't
    special-cased to accept a graph just for this caller.
    """
    if exclude_imports:
        yield None
        return
    merged_graph, report = ontology_evaluation.resolve_imports(
        main_ontology, import_dir, allow_network, ontology_evaluation.DEFAULT_IMPORT_GLOBS
    )
    if not report["resolved"]:
        yield None
        return
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "resolved-imports.ttl"
        merged_graph.serialize(destination=str(tmp_path), format="turtle")
        yield str(tmp_path)


def check_consistency(
    new_ontology: str | Path,
    *,
    old_ontology: Optional[str | Path] = None,
    tarql_sources: Iterable[str | Path] = (),
    ontology_paths: Optional[Iterable[str | Path]] = None,
    query_pattern: str = DEFAULT_QUERY_GLOBS,
    import_dir: Optional[str | Path] = None,
    exclude_imports: bool = False,
    allow_network: bool = False,
    ontology_target_for_stubs: Optional[str | Path] = None,
    verbose: bool = False,
) -> ConsistencyReport:
    """Runs whichever checks apply given the inputs provided:

    - `old_ontology` given -> version-diff + rename detection against `new_ontology`.
    - `tarql_sources` given -> TARQL/oxi-gen alignment against `ontology_paths`
      (default: just `new_ontology`), sharpened by any renames detected above.

    Import resolution (`import_dir`/`exclude_imports`/`allow_network`)
    applies to `old_ontology`, `new_ontology`, *and* (via
    `_resolved_imports_tempfile`) `new_ontology`'s resolved imports being
    folded into the TARQL-alignment ontology set too -- same convention as
    every other `--ontology`-taking stage in `pipeline.py`, now including
    this one. `verbose` prints which owl:imports resolved/didn't and which
    query files `tarql_sources` (a folder, `query_pattern`) actually
    expanded to, before running.
    """
    ontology_paths = list(ontology_paths) if ontology_paths is not None else [new_ontology]
    report = ConsistencyReport(new_ontology=str(new_ontology), old_ontology=str(old_ontology) if old_ontology else None)

    new_graph = load_ontology_graph(
        new_ontology, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network,
        verbose=verbose,
    )

    renames: List[TermRename] = []
    if old_ontology is not None:
        old_graph = load_ontology_graph(
            old_ontology, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network,
            verbose=verbose,
        )
        diff, bump = version_diff.diff_ontologies(old_graph, new_graph)
        report.ontology_diff = diff
        report.bump = bump
        renames = rename_detection.detect_renames(diff, new_graph)
        report.renames = renames

    tarql_sources = list(tarql_sources)
    if tarql_sources:
        if verbose:
            expanded = io_utils.expand_sources(tarql_sources, query_pattern)
            print(f"[verbose] {tarql_sources} (--file-pattern {query_pattern}): {len(expanded)} query file(s) matched:")
            for p in expanded:
                print(f"    {p}")
        with _resolved_imports_tempfile(new_ontology, import_dir, exclude_imports, allow_network) as imports_path:
            alignment_ontology_paths = ontology_paths + [imports_path] if imports_path else ontology_paths
            alignment = pa.check_tarql_ontology_alignment(
                tarql_sources, alignment_ontology_paths, query_pattern=query_pattern
            )
        report.alignment = alignment
        # Repairs are written back to the caller's own files, so they use
        # `ontology_paths` (unexpanded) -- the resolved-imports temp file is
        # only ever a read-only input to the alignment check above, never a
        # repair target.
        report.repairs = tarql_repair.suggest_repairs(
            alignment, ontology_paths, tarql_sources, renames=renames,
            ontology_target_for_stubs=ontology_target_for_stubs,
        )

    return report


def format_consistency_report(report: ConsistencyReport) -> str:
    lines: List[str] = []
    if report.ontology_diff is not None:
        assert report.bump is not None  # always set alongside ontology_diff by check_consistency
        lines.append(version_diff.format_report(
            report.ontology_diff, report.bump, report.old_ontology or "old", report.new_ontology
        ))
        lines.append("")

    if report.alignment is not None:
        lines.append(pa.format_alignment_report(report.alignment))
        lines.append("")

    if report.repairs:
        lines.append(f"{len(report.repairs)} suggested repair(s):")
        for suggestion in report.repairs:
            lines.append(f"  [{suggestion.kind}] {suggestion.target_file} (confidence {suggestion.confidence:.0%})")
            lines.append(f"    {suggestion.description}")
    elif report.alignment is not None:
        lines.append("No repairs suggested.")

    return "\n".join(lines)


def write_repair_patches(repairs: List[RepairSuggestion], out_dir: str | Path) -> List[Path]:
    """Writes each suggestion's unified diff to `out_dir/<n>-<kind>.patch`
    (dry-run artifact, doesn't touch the actual target files -- use
    `apply_repairs` for that). Returns the written paths, in the same order
    as `repairs`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, suggestion in enumerate(repairs, start=1):
        path = out_dir / f"{i:02d}-{suggestion.kind}-{Path(suggestion.target_file).name}.patch"
        path.write_text(suggestion.diff_text, encoding="utf-8")
        written.append(path)
    return written


def apply_repairs(repairs: List[RepairSuggestion], *, min_confidence: float = 0.0) -> List[RepairSuggestion]:
    """Writes every suggestion with `confidence >= min_confidence` to its
    `target_file`, in place. Returns the suggestions actually applied.
    There is no dry-run flag here on purpose -- `write_repair_patches` is
    the dry-run path; calling this means you've already decided to apply.
    """
    applied = [s for s in repairs if s.confidence >= min_confidence]
    for suggestion in applied:
        apply_suggestion(suggestion, write=True)
    return applied
