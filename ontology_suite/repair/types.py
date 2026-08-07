"""Shared result type for every kind of repair this package can suggest --
a registry-check quick fix (``checks.repair``), a TARQL prefix/rename fix,
or an ontology-declaration stub (``repair.tarql_repair``). One shape means
one CLI/report layer can present all three uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RepairSuggestion:
    id: str
    kind: str  # "update_prefix" | "rename_prefix" | "rename_iri" | "insert_ontology_stub" | "registry_repair"
    target_file: str
    description: str
    diff_text: str
    new_content: Optional[str] = None
    confidence: float = 1.0
    related_finding: Optional[str] = None


def apply_suggestion(suggestion: RepairSuggestion, write: bool = False) -> str:
    """Returns the target file's content after applying `suggestion`. If
    `write` is True, also writes it to `suggestion.target_file`. Raises if
    the suggestion has no computed replacement content (some findings --
    e.g. 'undeclared_namespace' -- are deliberately reported with no
    suggestion at all, since a safe fix can't be inferred; see
    ``tarql_repair``'s module docstring)."""
    if suggestion.new_content is None:
        raise ValueError(f"suggestion {suggestion.id!r} has no computed replacement content")
    if write:
        Path(suggestion.target_file).write_text(suggestion.new_content, encoding="utf-8")
    return suggestion.new_content
