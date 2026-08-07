"""Turns the findings from ``sketch.prefix_alignment`` (TARQL/oxi-gen query
vs. ontology drift) into concrete, appliable :class:`RepairSuggestion`\\ s --
the "suggested diffs" half of this package's consistency-checking mandate.

Three kinds of fix, one per finding shape:

- **Prefix/namespace drift** (``check_prefix_fixes``) -- a
  ``namespace_mismatch`` is fixed by rewriting the query's one `PREFIX`
  line to the ontology's actual IRI; a ``prefix_name_mismatch`` is fixed by
  renaming the query's prefix label to the one the ontology uses (cosmetic,
  lower confidence). ``undeclared_namespace`` findings get no suggestion --
  the given ontology set genuinely doesn't declare that namespace under any
  name, and guessing what the query *meant* isn't safe to automate (see
  ``docs/TARQL_ALIGNMENT.md``).

- **Renamed vocabulary** (``suggest_rename_fixes``) -- when an
  ``UndeclaredTerm`` finding's IRI matches the *old* side of a
  :class:`~ontology_suite.versioning.rename_detection.TermRename` (i.e. an
  ontology version-diff shows a plausible removed->added pairing, and the
  local name actually changed), rewrite every occurrence of the old
  term in the query to the new one. Pure namespace bumps (same local name,
  different namespace) are deliberately *not* handled here -- that's
  already the exact same fix `check_prefix_fixes` proposes for the
  matching ``namespace_mismatch`` finding, and handling it twice would
  produce two suggestions for one root cause (see
  ``docs/TARQL_ALIGNMENT.md``'s "one thing to expect, not a bug" note).

- **Genuinely new vocabulary** (``suggest_ontology_stubs``) -- an
  ``UndeclaredTerm`` with no matching rename at all is either a typo or
  vocabulary the ontology hasn't been given a declaration for yet; this
  proposes the latter (an ``owl:Class``/``rdf:Property`` stub appended to
  the ontology file) since that's the safe, reviewable default -- same
  spirit as ``checks/repair.py``'s STR-001/STR-002 templates, applied here
  to a real file on disk rather than an in-memory finding.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..sketch import prefix_alignment as pa
from ..sketch.tarql_visualiser import extract_prefixes
from ..versioning.rename_detection import TermRename
from . import text_edit
from .types import RepairSuggestion


def _suggest_namespace_mismatch_fix(
    finding: pa.PrefixMisalignment, text: str, ontology_candidates: Dict[str, str]
) -> Optional[RepairSuggestion]:
    if not ontology_candidates:
        return None
    target_iri = sorted(ontology_candidates)[0]
    confidence = 1.0 if len(ontology_candidates) == 1 else 0.5
    new_text = text_edit.replace_prefix_iri(text, finding.prefix, target_iri)
    if new_text == text:
        return None
    note = "" if len(ontology_candidates) == 1 else f" (ambiguous -- ontology set also binds '{finding.prefix}:' to {sorted(ontology_candidates)[1:]}, verify before applying)"
    return RepairSuggestion(
        id=f"prefix::{finding.tarql_file}::{finding.prefix}::namespace_mismatch",
        kind="update_prefix",
        target_file=finding.tarql_file,
        description=f"Update 'PREFIX {finding.prefix}:' to the ontology's <{target_iri}>{note}",
        diff_text=text_edit.unified_diff(text, new_text, finding.tarql_file),
        new_content=new_text,
        confidence=confidence,
        related_finding=finding.detail,
    )


def check_prefix_fixes(
    misalignments: List[pa.PrefixMisalignment], ontology_paths: Iterable[str | Path]
) -> List[RepairSuggestion]:
    """Entry point for prefix/namespace-drift repairs: re-resolves each
    finding's correct target against the *actual* ontology set
    (`ontology_paths`), since a `PrefixMisalignment` itself only carries the
    mismatch, not the fix.
    """
    ontology_ns = pa.load_ontology_namespaces(ontology_paths)
    by_file: Dict[str, str] = {}

    def _text(path: str) -> str:
        if path not in by_file:
            by_file[path] = Path(path).read_text(encoding="utf-8")
        return by_file[path]

    suggestions: List[RepairSuggestion] = []
    for finding in misalignments:
        text = _text(finding.tarql_file)

        if finding.kind == "namespace_mismatch":
            candidates = ontology_ns.by_prefix.get(finding.prefix, {})
            entry = _suggest_namespace_mismatch_fix(finding, text, candidates)
            if entry is not None:
                suggestions.append(entry)

        elif finding.kind == "prefix_name_mismatch":
            owners = ontology_ns.by_namespace.get(finding.tarql_namespace, {})
            if not owners:
                continue
            target_prefix = sorted(owners)[0]
            confidence = 0.6 if len(owners) == 1 else 0.4

            tarql_prefixes = extract_prefixes(text)
            if target_prefix in tarql_prefixes and tarql_prefixes[target_prefix] != finding.tarql_namespace:
                continue  # renaming would collide with an unrelated existing prefix in this file -- unsafe

            new_text = text_edit.rename_prefix_label(text, finding.prefix, target_prefix)
            if new_text == text:
                continue
            suggestions.append(RepairSuggestion(
                id=f"prefix::{finding.tarql_file}::{finding.prefix}::prefix_name_mismatch",
                kind="rename_prefix",
                target_file=finding.tarql_file,
                description=f"Rename prefix '{finding.prefix}:' to '{target_prefix}:' to match the ontology's own label for <{finding.tarql_namespace}>",
                diff_text=text_edit.unified_diff(text, new_text, finding.tarql_file),
                new_content=new_text,
                confidence=confidence,
                related_finding=finding.detail,
            ))
        # "undeclared_namespace": no safe automatic fix -- see module docstring.
    return suggestions


def suggest_rename_fixes(
    undeclared_terms: List[pa.UndeclaredTerm],
    renames: List[TermRename],
    tarql_paths: Iterable[str | Path],
) -> List[RepairSuggestion]:
    renames_by_key = {
        (r.kind, r.old_iri): r for r in renames if r.local_name_changed
    }
    if not renames_by_key or not undeclared_terms:
        return []

    suggestions: List[RepairSuggestion] = []
    for path in pa._expand_paths(tarql_paths, pa.DEFAULT_QUERY_GLOBS):
        text = path.read_text(encoding="utf-8")
        prefixes = extract_prefixes(text)
        new_text = text
        applied: List[TermRename] = []
        for term in undeclared_terms:
            rename = renames_by_key.get((term.kind, term.term))
            if rename is None:
                continue
            candidate = text_edit.replace_term(new_text, rename.old_iri, rename.new_iri, prefixes)
            if candidate != new_text:
                applied.append(rename)
                new_text = candidate
        if applied:
            suggestions.append(RepairSuggestion(
                id=f"rename::{path}",
                kind="rename_iri",
                target_file=str(path),
                description="Update to the renamed ontology term(s): " + "; ".join(f"{r.old_iri} -> {r.new_iri}" for r in applied),
                diff_text=text_edit.unified_diff(text, new_text, str(path)),
                new_content=new_text,
                confidence=min(r.confidence for r in applied),
                related_finding="; ".join(f"{r.reason}" for r in applied),
            ))
    return suggestions


_STUB_PREDICATE = {"class": "owl:Class", "property": "rdf:Property"}
_STUB_PREFIXES = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}


def suggest_ontology_stubs(
    undeclared_terms: List[pa.UndeclaredTerm],
    renames: List[TermRename],
    ontology_target_path: str | Path,
    misaligned_namespaces: Iterable[str] = (),
) -> List[RepairSuggestion]:
    """Proposes declaring, in `ontology_target_path`, every undeclared term
    that *isn't* explained by a rename (those are handled by
    `suggest_rename_fixes` instead) and isn't already covered by a
    `check_prefix_fixes` `namespace_mismatch` fix (`misaligned_namespaces`
    -- once that one-line PREFIX fix is applied, the term resolves to the
    ontology's real declaration and needs no stub of its own; otherwise the
    same root cause gets two conflicting suggestions, see
    ``docs/TARQL_ALIGNMENT.md``'s "one thing to expect, not a bug" note).
    What's left is "this is genuinely new vocabulary". Declares properties
    generically as `rdf:Property` (not guessing object- vs.
    datatype-property), matching `resources/repairs/STR-002.ru`'s own safe
    default.
    """
    renamed_old_iris = {r.old_iri for r in renames if r.local_name_changed}
    misaligned_namespaces = set(misaligned_namespaces)
    remaining = [
        t for t in undeclared_terms
        if t.term not in renamed_old_iris and text_edit.split_iri(t.term)[0] not in misaligned_namespaces
    ]
    if not remaining:
        return []

    ontology_target_path = Path(ontology_target_path)
    text = ontology_target_path.read_text(encoding="utf-8") if ontology_target_path.exists() else ""
    ontology_ns = pa.load_ontology_namespaces([ontology_target_path]) if ontology_target_path.exists() else None

    lines = ["", "# --- suggested declarations for vocabulary used in TARQL/oxi-gen queries but not yet declared ---"]
    for term in sorted(remaining, key=lambda t: (t.kind, t.term)):
        namespace, local = text_edit.split_iri(term.term)
        prefix = None
        if ontology_ns is not None:
            owners = ontology_ns.by_namespace.get(namespace, {})
            if owners:
                prefix = sorted(owners)[0]
        rendered = f"{prefix}:{local}" if prefix else f"<{term.term}>"
        lines.append(f"{rendered} a {_STUB_PREDICATE[term.kind]} .")
    stub_block = "\n".join(lines) + "\n"

    needed_prefix_lines = ""
    if "@prefix owl:" not in text and "PREFIX owl:" not in text.upper():
        needed_prefix_lines += f"@prefix owl: <{_STUB_PREFIXES['owl']}> .\n"
    if "@prefix rdf:" not in text and "PREFIX rdf:" not in text.upper():
        needed_prefix_lines += f"@prefix rdf: <{_STUB_PREFIXES['rdf']}> .\n"

    new_text = (needed_prefix_lines + text) if needed_prefix_lines else text
    new_text = new_text + stub_block

    return [RepairSuggestion(
        id=f"ontology_stub::{ontology_target_path}",
        kind="insert_ontology_stub",
        target_file=str(ontology_target_path),
        description=f"Declare {len(remaining)} term(s) used in TARQL/oxi-gen queries but never declared: "
                     + ", ".join(sorted(t.term for t in remaining)),
        diff_text=text_edit.unified_diff(text, new_text, str(ontology_target_path)),
        new_content=new_text,
        confidence=0.7,
        related_finding="; ".join(t.detail for t in remaining),
    )]


def suggest_repairs(
    alignment_report: pa.AlignmentReport,
    ontology_paths: Iterable[str | Path],
    tarql_paths: Iterable[str | Path],
    renames: Optional[List[TermRename]] = None,
    ontology_target_for_stubs: Optional[str | Path] = None,
) -> List[RepairSuggestion]:
    """Combines all three suggestion kinds for one `check_tarql_ontology_alignment`
    report. `renames` (from `versioning.rename_detection.detect_renames`, if
    an old/new ontology version-diff is available) sharpens undeclared-term
    findings into rename fixes instead of "declare this as new" stubs; pass
    `None` (the default) to always propose stubs for undeclared terms.
    `ontology_target_for_stubs` defaults to the first path in `ontology_paths`.
    """
    renames = renames or []
    suggestions = check_prefix_fixes(alignment_report.prefix_misalignments, ontology_paths)
    suggestions += suggest_rename_fixes(alignment_report.undeclared_terms, renames, tarql_paths)

    misaligned_namespaces = {
        f.tarql_namespace for f in alignment_report.prefix_misalignments if f.kind == "namespace_mismatch"
    }
    ontology_paths = list(ontology_paths)
    stub_target = ontology_target_for_stubs or (ontology_paths[0] if ontology_paths else None)
    if stub_target is not None:
        suggestions += suggest_ontology_stubs(alignment_report.undeclared_terms, renames, stub_target, misaligned_namespaces)

    return suggestions
