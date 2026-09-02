"""
Loads the shared check registry (registry.json) and provides helpers for
resolving a SHACL/SPARQL validation result back to the check that produced
it.

The registry is the single source of truth shared by:
  * the SHACL shapes (shapes/*.ttl)
  * the standalone SPARQL CONSTRUCT tests (sparql/**/*.rq)
  * this Python framework
  * the Rust framework

Every check has a stable id such as "STR-001". That id is used in three
different places depending on how a given check is implemented:

1. Standalone SPARQL CONSTRUCT tests always bind
   ``sh:sourceConstraintComponent`` directly to ``oq:<id>``.
2. SHACL shapes that are named exactly after the check (e.g. ``oq:STR-001``)
   surface the id through ``sh:sourceShape``.
3. SHACL shapes that had to be split into several NodeShapes (e.g.
   STR-003, QUA-001, STY-002) carry an explicit ``oq:checkId "STR-003"``
   annotation on each shape node; the id must be looked up in the shapes
   graph itself.

``resolve_check_id`` implements that three-step fallback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

# Must match the actual `PREFIX oq:` every shapes/*.ttl and sparql/**/*.rq
# file in this repo uses. registry.json's own "namespace" metadata field now
# carries this same IRI; for a long time it carried a different placeholder
# in each of the two copies of the registry, and neither was the one the
# data asserts -- harmless only because nothing reads the field, which is
# exactly how it stayed wrong through both.
# Only `resolve_check_id`'s oq:checkId lookup (step 3/4 below) needs
# this to be exactly right -- the two local-name-only steps before it work
# regardless of namespace -- but when it's wrong, that lookup silently
# matches nothing. This was caught as a real, previously-unnoticed bug: it
# had *always* silently failed for every checkId-annotated split shape
# (STR-003/QUA-001/STY-002). Each affected check also has a portable SPARQL
# twin (sh:sourceConstraintComponent-based, unaffected by this) that
# produces the *same* findings correctly resolved -- so every SHACL-sourced
# row with check_id=None sat right alongside a correctly-resolved SPARQL
# row for the identical finding (merge.py's dedup key includes check_id, so
# they don't collapse into one). Both rows were always present in
# full_results.csv; the bug went unnoticed simply because nothing had
# specifically checked for check_id=None rows until this was run against a
# real, large ontology and 435 of them showed up in one report.
OQ = Namespace("https://semantechs.co.uk/ontology-quality/")
SH = Namespace("http://www.w3.org/ns/shacl#")


@dataclass
class Check:
    id: str
    category: str
    metric: str
    default_severity: str
    title: str
    description: str
    remediation: str
    cucumber_feature: str
    cucumber_scenario: str


class Registry:
    def __init__(self, checks: Dict[str, Check], namespace: str):
        self.checks = checks
        self.namespace = namespace

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        checks = {}
        for entry in data["checks"]:
            checks[entry["id"]] = Check(**entry)
        return cls(checks=checks, namespace=data["namespace"])

    def get(self, check_id: str) -> Optional[Check]:
        return self.checks.get(check_id)

    def all_ids(self):
        return list(self.checks.keys())

    def categories(self):
        seen = []
        for c in self.checks.values():
            if c.category not in seen:
                seen.append(c.category)
        return seen

    def _local_name(self, iri: str) -> str:
        iri = str(iri)
        for sep in ("#", "/"):
            if sep in iri:
                return iri.rsplit(sep, 1)[-1]
        return iri

    def resolve_check_id(
        self,
        source_constraint_component: Optional[URIRef],
        source_shape: Optional[URIRef],
        shapes_graph: Optional[Graph] = None,
    ) -> Optional[str]:
        """Resolve the registry check id for one validation result.

        Tries, in order:
          1. sourceConstraintComponent local name directly matches an id
             (this is how standalone SPARQL CONSTRUCT results are tagged).
          2. sourceShape local name directly matches an id (this is how
             most SHACL-SPARQL shapes, named exactly after their check,
             are tagged).
          3. An ``oq:checkId`` annotation on the sourceShape node, looked
             up in the shapes graph (this is how split shapes such as
             STR-003 / QUA-001 / STY-002 are tagged).
          4. If none of the above match *and* ``source_shape`` is itself a
             nested property shape (e.g. an ``sh:property [ ... ]`` blank
             node), walk up to the enclosing ``NodeShape`` that references
             it via ``sh:property`` and retry steps 2-3 against *that*.
             pyshacl reports a property-constraint violation's
             ``sh:sourceShape`` as the nested blank-node property shape
             itself, not the enclosing shape the ``oq:checkId`` annotation
             is conventionally placed on -- without this, every check
             authored with a nested ``sh:property [...]`` constraint (most
             of this suite's SHACL shapes) resolves to no check id at all.
             Caught as a real bug: 435 of 894 findings against a real
             gist-importing ontology came back with no check id, category,
             or remediation text -- every single native-SHACL-core finding
             in the suite.
        """
        if source_constraint_component is not None:
            local = self._local_name(source_constraint_component)
            if local in self.checks:
                return local

        if source_shape is not None and shapes_graph is not None:
            return self._resolve_shape_id(source_shape, shapes_graph)

        if source_shape is not None:
            local = self._local_name(source_shape)
            if local in self.checks:
                return local

        return None

    def _resolve_shape_id(
        self, shape: URIRef, shapes_graph: Graph, _visited: Optional[set] = None
    ) -> Optional[str]:
        _visited = _visited if _visited is not None else set()
        if shape in _visited:
            return None
        _visited.add(shape)

        local = self._local_name(shape)
        if local in self.checks:
            return local

        for _, _, val in shapes_graph.triples((shape, OQ.checkId, None)):
            candidate = str(val)
            if candidate in self.checks:
                return candidate

        for parent in shapes_graph.subjects(SH.property, shape):
            candidate = self._resolve_shape_id(parent, shapes_graph, _visited)
            if candidate is not None:
                return candidate

        return None
