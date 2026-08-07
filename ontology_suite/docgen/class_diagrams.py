"""Generates a per-class Graphviz diagram (`.dot`/`.svg`/`.png`) plus the
class's own concise bounded description (`.ttl`) for docgen's HTML output.

Reuses `sketch.dot_export` for the actual rendering -- the same blank-node/
literal handling built for `pattern_consistency`'s consistency-gap diagrams
(see `docs/MODELLING_PATTERN_CONSISTENCY.md`) -- rather than a bespoke DOT
generator, so every diagram this suite produces shares one visual language.
No consistency checking happens here, so every node/edge uses the plain
default (unhighlighted) colours -- there is no "gap" concept for a single
class's own definition.

Scope, by design: only classes declared in the ontology's own local
namespace get a diagram by default -- an ontology importing gist has no
business generating one of these for every one of gist's ~96 classes every
time its own documentation is rebuilt. Pass `include_external=True`
(`--diagram-imports` on the CLI) to also diagram classes referenced via
`--ref` (docgen's existing "resolve external-term definitions" ontology
files) -- the same files double as "here's what an external class's own
triples look like" for this purpose.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import rdflib
from rdflib import OWL, RDF, RDFS

from ..sketch import dot_export

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_filename(curie_or_iri: str) -> str:
    """A filesystem-safe stem for a class's diagram files, derived from
    its curie's local name (e.g. "ex:Vehicle" -> "Vehicle") or, failing
    that, the tail of the full IRI with unsafe characters collapsed."""
    local = curie_or_iri.split(":")[-1] if ":" in curie_or_iri else curie_or_iri
    local = local.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    safe = _SAFE_NAME.sub("_", local).strip("_")
    return safe or "class"


def concise_bounded_description(graph: rdflib.Graph, resource: rdflib.URIRef) -> rdflib.Graph:
    """The resource's own CBD: every triple it's the subject of, plus --
    recursively -- every triple any blank-node object of those leads to
    (an `owl:Restriction`, an `owl:unionOf`/`intersectionOf` list cell,
    etc.). Named-node objects (a superclass, the property a restriction
    is `owl:onProperty`, ...) are included as that edge's endpoint but not
    expanded past the one hop, so a local class's diagram shows its
    relationship to e.g. a `gist:` superclass without pulling in gist's
    own full definition of that superclass too.
    """
    cbd = rdflib.Graph(bind_namespaces="none")
    for prefix, ns in graph.namespaces():
        cbd.bind(prefix, ns)
    seen_bnodes = set()
    frontier = [resource]
    while frontier:
        node = frontier.pop()
        for p, o in graph.predicate_objects(node):
            cbd.add((node, p, o))
            if isinstance(o, rdflib.BNode) and o not in seen_bnodes:
                seen_bnodes.add(o)
                frontier.append(o)
    return cbd


def _trim_for_diagram(graph: rdflib.Graph, resource: rdflib.URIRef) -> rdflib.Graph:
    """A copy of `graph` with a couple of triples about `resource` itself
    removed, for diagram *rendering* only -- true of every class
    diagrammed this way, so showing them is pure repetition, not
    information:

    - `resource rdf:type owl:Class` -- the whole reason it's being
      diagrammed in the first place.
    - `resource rdfs:isDefinedBy <...>` -- which ontology a term belongs
      to is already the diagram's entire context (it's *this* ontology's
      documentation), regardless of what that ontology's IRI happens to
      be -- dropped by predicate, not by exact triple match, since the
      object varies per ontology.

    The full CBD (including both) still goes into the `.ttl` export --
    that's a complete data artifact, not a picture, and shouldn't silently
    omit a real triple the class actually has.
    """
    trimmed = rdflib.Graph(bind_namespaces="none")
    for prefix, ns in graph.namespaces():
        trimmed.bind(prefix, ns)
    for s, p, o in graph:
        if (s, p, o) == (resource, RDF.type, OWL.Class):
            continue
        if s == resource and p == RDFS.isDefinedBy:
            continue
        trimmed.add((s, p, o))
    return trimmed


@dataclass
class ClassDiagram:
    class_id: str  # curie, matches doc_data's class/externalReuse "id"
    dot_path: Path
    ttl_path: Path
    svg_path: Optional[Path] = None
    png_path: Optional[Path] = None


def generate_class_diagram(
    graph: rdflib.Graph,
    class_iri: rdflib.URIRef,
    class_curie: str,
    out_dir: Path,
    *,
    render_images: bool = True,
) -> ClassDiagram:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(class_curie)
    cbd = concise_bounded_description(graph, class_iri)

    dot_path = out_dir / f"{stem}.dot"
    dot_export.write_dot(_trim_for_diagram(cbd, class_iri), dot_path, graph_name=f"class_{stem}")

    ttl_path = out_dir / f"{stem}.ttl"
    cbd.serialize(destination=str(ttl_path), format="turtle")

    svg_path = png_path = None
    if render_images:
        svg_path = out_dir / f"{stem}.svg"
        png_path = out_dir / f"{stem}.png"
        for target, fmt in ((svg_path, "svg"), (png_path, "png")):
            subprocess.run(["dot", f"-T{fmt}", str(dot_path), "-o", str(target)], check=True, capture_output=True)

    return ClassDiagram(class_id=class_curie, dot_path=dot_path, ttl_path=ttl_path, svg_path=svg_path, png_path=png_path)


def _expand(curie_or_iri: str, ns_by_prefix: Dict[str, str]) -> Optional[rdflib.URIRef]:
    if curie_or_iri.startswith("http://") or curie_or_iri.startswith("https://"):
        return rdflib.URIRef(curie_or_iri)
    if ":" not in curie_or_iri:
        return None
    prefix, local = curie_or_iri.split(":", 1)
    ns = ns_by_prefix.get(prefix)
    return rdflib.URIRef(ns + local) if ns else None


def generate_class_diagrams(
    graph: rdflib.Graph,
    doc_data: dict,
    out_dir: Path,
    *,
    include_external: bool = False,
    render_images: bool = True,
) -> Dict[str, ClassDiagram]:
    """Generate one diagram per local class in `doc_data["classes"]`
    (always), plus every external class in `doc_data["externalReuse"]`
    if `include_external`. `graph` must already contain the external
    classes' own triples for that to produce anything more than an empty
    diagram -- e.g. the merged ontology + `--ref` files.

    Returns `{class_curie: ClassDiagram}`; the caller is responsible for
    patching `doc_data` with the (relative) file paths -- see
    `pipeline.run_docgen_stage` -- before it's written/rendered.
    """
    ns_by_prefix = {ns["prefix"]: ns["uri"] for ns in doc_data["namespaces"]}

    targets: List[str] = [c["id"] for c in doc_data["classes"]]
    if include_external:
        targets += [r["id"] for r in doc_data["externalReuse"] if r["kind"] == "Class"]

    results: Dict[str, ClassDiagram] = {}
    for class_curie in targets:
        iri = _expand(class_curie, ns_by_prefix)
        if iri is None:
            continue
        results[class_curie] = generate_class_diagram(
            graph, iri, class_curie, out_dir, render_images=render_images
        )
    return results


def patch_doc_data_with_diagrams(doc_data: dict, generated: Dict[str, ClassDiagram], out_dir: Path) -> None:
    """Add a `"diagram": {"svg": ..., "png": ..., "ttl": ...}` field (paths
    relative to `out_dir`, using `/` regardless of platform, for direct use
    as an `<img src>`/`<a href>` in the generated HTML) to every class/
    externalReuse entry a diagram was generated for. Mutates `doc_data` in
    place.
    """
    def relativize(p: Optional[Path]) -> Optional[str]:
        return p.relative_to(out_dir).as_posix() if p else None

    for entry_list in (doc_data["classes"], doc_data["externalReuse"]):
        for entry in entry_list:
            cd = generated.get(entry["id"])
            if cd:
                entry["diagram"] = {
                    "svg": relativize(cd.svg_path),
                    "png": relativize(cd.png_path),
                    "ttl": relativize(cd.ttl_path),
                }
