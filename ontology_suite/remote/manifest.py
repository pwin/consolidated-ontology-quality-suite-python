"""Fuseki (and SPARQL 1.1 Protocol stores generally) has no built-in link
from a named graph back to the local TARQL/oxi-gen query file that produced
it, or from a data graph to the ontology graph it's meant to conform to --
that provenance has to be supplied out of band. A :class:`GraphManifest` is
where a project records it (by hand, or generated once via
`triplify.discovery`'s own CSV/tarql stem-pairing convention, if a
project's local files and its Fuseki graph names follow the same naming).

Given that binding, `check_named_graph_consistency` runs all *three*
consistency questions this suite can ask about one triplified named graph,
each catching a different failure mode:

1. **Template vs. ontology** (static) -- does the TARQL/oxi-gen query file
   on disk reference only classes/properties the ontology graph actually
   declares? Reuses `sketch.prefix_alignment.check_undeclared_terms`
   (IRI-identity based) against the ontology graph materialized from the
   store. The *prefix*-label half of `sketch.prefix_alignment`
   (`check_tarql_ontology_prefix_alignment`) is deliberately **not** run
   here: a SPARQL store holds triples, not `@prefix` declarations, so a
   materialized graph has no ontology-author-chosen prefix labels to
   compare against in the first place -- CONSTRUCTing a named graph back
   out and reserializing it is a lossy round-trip for that information
   even though every triple survives byte-for-byte. Prefix-alignment
   checking still applies to the *local* case (`consistency.check_consistency`)
   where an actual `.ttl` file with real `@prefix` lines is available.
2. **Live data vs. ontology** -- does what's *actually* sitting in the named
   graph right now conform to the ontology's declarations? Catches
   real-world drift the static template can't (a CSV value that triplifies
   into something the template's own shape doesn't reveal, a stale graph
   that was never re-triplified after the query *was* fixed, ...).
3. **Template vs. live data** -- do the classes/properties the query
   template *says* it builds match what's actually in the named graph? A
   mismatch here, independent of what the ontology says, points at an
   oxi-gen/triplify execution problem (wrong file loaded, a stale run, an
   upstream CSV schema change) rather than a modelling problem.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

from rdflib import RDF

from ..dataquality import data_quality
from ..sketch import prefix_alignment as pa
from . import fuseki

GraphRole = Literal["ontology", "triplified_data"]


@dataclass
class GraphBinding:
    graph_uri: str
    role: GraphRole
    source_tarql: Optional[str] = None
    """Local path to the TARQL/oxi-gen query file that produced this graph (role='triplified_data' only)."""
    ontology_graph_uri: Optional[str] = None
    """Which named graph (role='ontology') this data graph should conform to (role='triplified_data' only)."""
    notes: Optional[str] = None


@dataclass
class GraphManifest:
    bindings: List[GraphBinding] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "GraphManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(bindings=[GraphBinding(**entry) for entry in data["graphs"]])

    def save(self, path: str | Path) -> None:
        payload = {"graphs": [vars(b) for b in self.bindings]}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def ontology_bindings(self) -> List[GraphBinding]:
        return [b for b in self.bindings if b.role == "ontology"]

    def data_bindings(self) -> List[GraphBinding]:
        return [b for b in self.bindings if b.role == "triplified_data"]


@dataclass
class NamedGraphConsistencyReport:
    graph_uri: str
    source_tarql: Optional[str]
    ontology_graph_uri: Optional[str]
    template_vs_ontology: Optional[List[pa.UndeclaredTerm]] = None
    live_data_vs_ontology: Optional[dict] = None
    template_vs_live_data: Optional[Dict[str, List[str]]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        if self.template_vs_ontology:
            return False
        if self.live_data_vs_ontology is not None and (
            self.live_data_vs_ontology["undeclared_classes_used"] or self.live_data_vs_ontology["undeclared_properties_used"]
        ):
            return False
        if self.template_vs_live_data is not None and any(self.template_vs_live_data.values()):
            return False
        return True


def check_named_graph_consistency(
    dataset: fuseki.FusekiDataset,
    binding: GraphBinding,
    *,
    sample_limit: Optional[int] = None,
) -> NamedGraphConsistencyReport:
    """Runs the three checks described in the module docstring for one
    `binding` (`binding.role` must be `"triplified_data"`). `sample_limit`
    bounds how many triples are pulled when materializing the data graph
    (see `fuseki.load_named_graph`'s own caveat about this not being a true
    CBD sample) -- omit it for graphs known to be small (the common case:
    one triplify job's output).
    """
    if binding.role != "triplified_data":
        raise ValueError(f"check_named_graph_consistency needs a 'triplified_data' binding, got role={binding.role!r}")

    report = NamedGraphConsistencyReport(
        graph_uri=binding.graph_uri, source_tarql=binding.source_tarql, ontology_graph_uri=binding.ontology_graph_uri
    )

    if not binding.ontology_graph_uri:
        report.warnings.append("no ontology_graph_uri bound -- skipping template-vs-ontology and live-data-vs-ontology checks")
    if not binding.source_tarql:
        report.warnings.append("no source_tarql bound -- skipping template-vs-ontology and template-vs-live-data checks")

    if binding.ontology_graph_uri:
        ontology_graph = fuseki.load_named_graph(dataset, binding.ontology_graph_uri)

        if binding.source_tarql:
            with tempfile.TemporaryDirectory() as tmp:
                ontology_path = Path(tmp) / "ontology.ttl"
                ontology_graph.serialize(destination=str(ontology_path), format="turtle")
                report.template_vs_ontology = pa.check_undeclared_terms([binding.source_tarql], [ontology_path])

        data_graph = fuseki.load_named_graph(dataset, binding.graph_uri, limit=sample_limit)
        declarations = data_quality.ontology_declarations(ontology_graph)
        report.live_data_vs_ontology = data_quality.check_conformance(declarations, data_graph)

    if binding.source_tarql:
        template_sketch = pa.build_sketch_graph([binding.source_tarql])
        template_classes = {str(o) for _, _, o in template_sketch.triples((None, RDF.type, None))}
        template_properties = {str(p) for p in template_sketch.predicates()}

        live_usage = fuseki.graph_predicate_and_type_usage(dataset, binding.graph_uri)
        live_classes, live_properties = set(live_usage["classes"]), set(live_usage["properties"])

        report.template_vs_live_data = {
            "classes_only_in_template": sorted(template_classes - live_classes),
            "classes_only_in_live_data": sorted(live_classes - template_classes),
            "properties_only_in_template": sorted(template_properties - live_properties),
            "properties_only_in_live_data": sorted(live_properties - template_properties),
        }

    return report


def check_manifest_consistency(
    dataset: fuseki.FusekiDataset, manifest: GraphManifest, *, sample_limit: Optional[int] = None
) -> List[NamedGraphConsistencyReport]:
    """Runs `check_named_graph_consistency` for every `"triplified_data"`
    binding in `manifest`."""
    return [check_named_graph_consistency(dataset, b, sample_limit=sample_limit) for b in manifest.data_bindings()]
