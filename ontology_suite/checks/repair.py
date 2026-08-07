"""Schematron-Quick-Fix-style repair engine for the registry-driven
SHACL+SPARQL suite (``checks/registry.py``, ``checks/merge.py``): each
check's remediation is a real SPARQL 1.1 Update template
(``resources/repairs/*.ru``), bridged to a specific finding via the same
``ResultRow`` shape every check engine already normalizes to
(focus_node/path/value), plus the resolved project standards (the
"$variables" a Schematron quick-fix would draw on).

Ported from ``consolidated_ontology_suite_webapp/src/checks/repairEngine.ts``:
the same manifest/template files, the same ``VALUES``-injection strategy for
binding variables into an arbitrary template's ``WHERE`` clause, and the same
insert-vs-replace outcome split -- but applied with rdflib's own SPARQL 1.1
Update support against an in-memory ``Graph`` (no oxigraph dependency needed
here; the JS side uses oxigraph because that's what runs in a VS Code
extension host). The same computed update text is also what
``ontology_suite.remote.fuseki.apply_update_remote`` posts to a live Fuseki
``/update`` endpoint -- one template, two execution backends.

Variable contract every template may reference (all pre-bound via a single
injected VALUES row -- see ``build_repair_update`` -- UNDEF where not
applicable to a given check):
    ?focusNode, ?path, ?value        -- from the finding's ResultRow
    ?derivedLabel                    -- humanized local name of ?focusNode
    ?defaultLanguageTag              -- ProjectStandards.default_language_tag (plain string literal)
    ?categoryClass                   -- ProjectStandards.category_class, resolved to a full IRI
    ?defaultOntologyBaseIri          -- ProjectStandards.default_ontology_base_iri (IRI)
    ?defaultVersionInfo              -- ProjectStandards.default_version_info (plain string literal)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from rdflib import Graph

from .merge import ResultRow
from .project_standards import ProjectStandards, resolve_standards_iris

WHERE_BLOCK = re.compile(r"\bWHERE\s*\{", re.IGNORECASE)

# manifest.json's "policyStandardsKey" values are the webapp's own camelCase
# ProjectStandards field names (projectStandardsCore.ts); project_standards.py
# ports the same fields to snake_case, so the manifest's key needs translating
# to look them up via getattr.
_POLICY_STANDARDS_KEY_MAP = {
    "equivalentClassPolicy": "equivalent_class_policy",
    "redundantEquivalencePolicy": "redundant_equivalence_policy",
}


@dataclass
class RepairManifestEntry:
    kind: str  # "insert" | "replace"
    title: str
    template: Optional[str] = None
    templates_by_policy: Optional[Dict[str, str]] = None
    policy_standards_key: Optional[str] = None


@dataclass
class RepairOutcome:
    check_id: str
    kind: str  # "insert" | "replace"
    title: str
    added_quads: List[tuple] = field(default_factory=list)
    removed_quads: List[tuple] = field(default_factory=list)
    result_graph: Graph = field(default_factory=Graph)
    update_text: str = ""


_manifest_cache: Dict[str, Dict[str, RepairManifestEntry]] = {}


def load_manifest(repairs_root_dir: str | Path) -> Dict[str, RepairManifestEntry]:
    key = str(Path(repairs_root_dir))
    if key in _manifest_cache:
        return _manifest_cache[key]
    data = json.loads((Path(repairs_root_dir) / "manifest.json").read_text(encoding="utf-8"))
    checks: Dict[str, RepairManifestEntry] = {}
    for check_id, entry in data["checks"].items():
        checks[check_id] = RepairManifestEntry(
            kind=entry["kind"],
            title=entry["title"],
            template=entry.get("template"),
            templates_by_policy=entry.get("templatesByPolicy"),
            policy_standards_key=entry.get("policyStandardsKey"),
        )
    _manifest_cache[key] = checks
    return checks


def has_repair_template(repairs_root_dir: str | Path, check_id: str) -> bool:
    """True if a Quick Fix can be offered for this check at all (regardless of whether the current row qualifies)."""
    return check_id in load_manifest(repairs_root_dir)


def _resolve_template_file(
    repairs_root_dir: str | Path, check_id: str, standards: ProjectStandards
) -> Optional[tuple]:
    entry = load_manifest(repairs_root_dir).get(check_id)
    if entry is None:
        return None
    template_name = entry.template
    if not template_name and entry.templates_by_policy and entry.policy_standards_key:
        attr_name = _POLICY_STANDARDS_KEY_MAP.get(entry.policy_standards_key, entry.policy_standards_key)
        policy = getattr(standards, attr_name)
        template_name = entry.templates_by_policy.get(policy)
    if not template_name:
        return None
    return Path(repairs_root_dir) / template_name, entry.title, entry.kind


def humanize_local_name(iri: str) -> str:
    """Humanizes an IRI's local name for use as a fallback rdfs:label/skos:prefLabel (e.g. "hasOwner" -> "has Owner")."""
    local = iri
    for sep in ("#", "/"):
        if sep in local:
            local = local.rsplit(sep, 1)[-1]
    local = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local)
    local = re.sub(r"[_-]+", " ", local)
    return local.strip()


def _sparql_string_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _format_iri_or_undef(iri: Optional[str]) -> str:
    return f"<{iri}>" if iri else "UNDEF"


def build_repair_update(
    template_text: str,
    row: ResultRow,
    standards: ProjectStandards,
    resolved_standards_iris: Dict[str, str],
) -> str:
    match = WHERE_BLOCK.search(template_text)
    if not match:
        raise ValueError("Repair template has no WHERE clause")

    variables = [
        "?focusNode", "?path", "?value", "?derivedLabel", "?defaultLanguageTag",
        "?categoryClass", "?defaultOntologyBaseIri", "?defaultVersionInfo",
    ]
    values = [
        _format_iri_or_undef(row.focus_node),
        _format_iri_or_undef(row.path),
        _format_iri_or_undef(row.value),
        _sparql_string_literal(humanize_local_name(row.focus_node)),
        _sparql_string_literal(standards.default_language_tag),
        _format_iri_or_undef(resolved_standards_iris.get("categoryClass")),
        _format_iri_or_undef(resolved_standards_iris.get("defaultOntologyBaseIri")),
        _sparql_string_literal(standards.default_version_info),
    ]
    values_clause = f"VALUES ({' '.join(variables)}) {{\n    ({' '.join(values)})\n  }}"

    insert_at = match.end()
    return f"{template_text[:insert_at]}\n  {values_clause}\n{template_text[insert_at:]}"


def compute_repair(
    repairs_root_dir: str | Path,
    row: ResultRow,
    document_graph: Graph,
    document_prefixes: Dict[str, str],
    standards: Optional[ProjectStandards] = None,
) -> Optional[RepairOutcome]:
    """Runs the repair for a single finding against the document's own graph
    (an isolated in-memory copy, not the workspace-wide merged graph -- so a
    fix never reaches across file boundaries) and returns the resulting
    delta. Returns ``None`` if the check has no repair template, or if the
    finding's check_id is missing.
    """
    if standards is None:
        standards = ProjectStandards()
    if not row.check_id:
        return None
    resolved = _resolve_template_file(repairs_root_dir, row.check_id, standards)
    if resolved is None:
        return None
    template_file, title, kind = resolved

    template_text = template_file.read_text(encoding="utf-8")
    resolved_standards_iris = resolve_standards_iris(standards, document_prefixes)
    update_text = build_repair_update(template_text, row, standards, resolved_standards_iris)

    working = Graph()
    for triple in document_graph:
        working.add(triple)
    before = set(working)
    working.update(update_text)
    after = set(working)

    return RepairOutcome(
        check_id=row.check_id,
        kind=kind,
        title=title,
        added_quads=sorted(after - before, key=str),
        removed_quads=sorted(before - after, key=str),
        result_graph=working,
        update_text=update_text,
    )


def render_added_turtle(added_quads: List[tuple], prefixes: Dict[str, str]) -> str:
    """Renders ``added_quads`` as a standalone Turtle block, grouped by
    subject, IRIs shrunk to ``prefixes`` where possible -- the text an
    'insert'-kind repair appends to the end of the source file, preserving
    the rest of its hand-authored formatting exactly (mirrors
    ``applyRepair.ts``'s ``renderAddedQuadsTurtle``)."""
    if not added_quads:
        return ""
    scratch = Graph()
    for p, iri in prefixes.items():
        scratch.bind(p, iri, override=True)
    for s, p, o in added_quads:
        scratch.add((s, p, o))
    by_subject: Dict[str, List[tuple]] = {}
    for s, p, o in added_quads:
        by_subject.setdefault(str(s), []).append((s, p, o))

    lines = [""]
    for subject, quads in by_subject.items():
        lines.append(scratch.namespace_manager.normalizeUri(subject) if not subject.startswith("_:") else f"_:{subject}")
        for i, (s, p, o) in enumerate(quads):
            suffix = "." if i == len(quads) - 1 else ";"
            pred = "a" if str(p) == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type" else scratch.namespace_manager.normalizeUri(p)
            lines.append(f"  {pred} {o.n3(scratch.namespace_manager)} {suffix}")
        lines.append("")
    return "\n".join(lines)


def apply_repair_to_file(
    outcome: RepairOutcome,
    document_path: str | Path,
    document_prefixes: Dict[str, str],
    rdf_format: str = "turtle",
    write: bool = False,
) -> str:
    """Computes the patched file content for `outcome` and, if ``write`` is
    True, writes it to `document_path`. Always returns the new content.

    'insert'-kind fixes are appended as a new Turtle block (preserves the
    rest of the file's formatting/comments exactly). 'replace'-kind fixes
    (a DELETE+INSERT touching existing triples) can't in general be spliced
    into arbitrary existing syntax without a real parser-preserving editor,
    so the whole document is reserialized from `outcome.result_graph`
    instead -- this *will* lose hand-authored formatting/comments (mirrors
    ``applyRepair.ts``; the caller should warn the user before applying).
    """
    document_path = Path(document_path)
    if outcome.kind == "insert":
        block = render_added_turtle(outcome.added_quads, document_prefixes)
        new_content = document_path.read_text(encoding="utf-8") + block if document_path.exists() else block
    else:
        new_content = outcome.result_graph.serialize(format=rdf_format)
    if write:
        document_path.write_text(new_content, encoding="utf-8")
    return new_content
