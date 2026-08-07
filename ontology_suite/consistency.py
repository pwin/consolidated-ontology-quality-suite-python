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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

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
) -> ConsistencyReport:
    """Runs whichever checks apply given the inputs provided:

    - `old_ontology` given -> version-diff + rename detection against `new_ontology`.
    - `tarql_sources` given -> TARQL/oxi-gen alignment against `ontology_paths`
      (default: just `new_ontology`), sharpened by any renames detected above.

    Import resolution (`import_dir`/`exclude_imports`/`allow_network`)
    applies to both `old_ontology` and `new_ontology`, same convention as
    every other `--ontology`-taking stage in `pipeline.py`.
    """
    ontology_paths = list(ontology_paths) if ontology_paths is not None else [new_ontology]
    report = ConsistencyReport(new_ontology=str(new_ontology), old_ontology=str(old_ontology) if old_ontology else None)

    new_graph = load_ontology_graph(
        new_ontology, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network
    )

    renames: List[TermRename] = []
    if old_ontology is not None:
        old_graph = load_ontology_graph(
            old_ontology, import_dir=import_dir, exclude_imports=exclude_imports, allow_network=allow_network
        )
        diff, bump = version_diff.diff_ontologies(old_graph, new_graph)
        report.ontology_diff = diff
        report.bump = bump
        renames = rename_detection.detect_renames(diff, new_graph)
        report.renames = renames

    tarql_sources = list(tarql_sources)
    if tarql_sources:
        alignment = pa.check_tarql_ontology_alignment(tarql_sources, ontology_paths, query_pattern=query_pattern)
        report.alignment = alignment
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
