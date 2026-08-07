"""Project-local configuration a workspace can supply to complete
mechanically-generated repairs -- the same role Schematron Quick Fix's (SQF)
``$variables`` play, resolved from the failing rule's own context. There is
no rule-local scope the way XSLT/XPath has, so instead every repair template
(``resources/repairs/*.ru``) gets these bound as ``VALUES``-injected
variables alongside the finding's own focus_node/path/value -- see
``checks/repair.py``.

Ported from ``consolidated_ontology_suite_webapp/src/checks/projectStandardsCore.ts``,
line for line where the concept carries over; IRI/CURIE resolution uses
rdflib's namespace machinery instead of the webapp's own ``expand``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Literal

GIST = "https://ontologies.semanticarts.com/gist/"


@dataclass
class ProjectStandards:
    """Language tag used when a repair adds a label/prefLabel and none exists yet, or replaces an untagged one."""
    default_language_tag: str = "en"
    """CURIE or full IRI of the class used for MDL-003's "model as a category instead of a class" repair."""
    category_class: str = "gist:Category"
    """Extra prefix bindings (beyond the document's own) needed to resolve category_class and other CURIE-shaped fields."""
    prefixes: Dict[str, str] = field(default_factory=lambda: {"gist": GIST})
    """MDL-002 policy: replace a named-to-named owl:equivalentClass with this predicate."""
    equivalent_class_policy: Literal["subClassOf", "closeMatch"] = "subClassOf"
    """LOG-003 policy: which axiom to keep when equivalentClass and subClassOf are redundantly both asserted."""
    redundant_equivalence_policy: Literal["keepEquivalentClass", "keepSubClassOf"] = "keepEquivalentClass"
    """Base IRI used for QUA-005's "declare an owl:Ontology resource" repair when the document doesn't already suggest one."""
    default_ontology_base_iri: str = "https://example.org/ontology/"
    """Literal value used for QUA-002's owl:versionInfo repair."""
    default_version_info: str = "0.1.0"


DEFAULT_PROJECT_STANDARDS = ProjectStandards()


def _expand(value: str, prefixes: Dict[str, str]) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if ":" not in value:
        return value
    prefix, local = value.split(":", 1)
    base = prefixes.get(prefix)
    return f"{base}{local}" if base is not None else value


def resolve_standards_iris(standards: ProjectStandards, document_prefixes: Dict[str, str]) -> Dict[str, str]:
    """Resolves every CURIE-shaped standards value against the standards' own
    prefixes plus the target document's declared prefixes."""
    all_prefixes = {**standards.prefixes, **document_prefixes}
    return {
        "categoryClass": _expand(standards.category_class, all_prefixes),
        "defaultOntologyBaseIri": standards.default_ontology_base_iri,
    }
