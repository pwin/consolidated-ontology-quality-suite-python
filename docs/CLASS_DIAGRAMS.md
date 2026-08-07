# Per-class diagrams in `docgen`

`docgen` already renders three whole-ontology Mermaid diagrams (class
hierarchy, alignment with imported/external terms, object-property graph --
see `docs/ARCHITECTURE.md`'s docgen section), computed client-side from the
flat class/property JSON arrays every time the HTML page loads. Those are
useful for an overview but don't scale down to "show me exactly this one
class's own definition" the way a focused diagram does. `docgen.class_diagrams`
adds that: one Graphviz diagram per class, generated at build time as real
`.svg`/`.png` files (plus the class's own `.dot` and a `.ttl` of just its
own triples), embedded directly into each class's card in the generated
`ontology-documentation.html`.

## Scope: local classes only, by default

Only classes declared in the ontology's own local namespace get a diagram
automatically -- the same `classes` array `extract_ontology_data.py`
already computes (see `docs/ARCHITECTURE.md`/`extract_ontology_data.py`'s
own `local_ns` detection). An ontology importing gist has no business
generating one of these for every one of gist's ~96 classes every time its
own documentation is rebuilt. Pass `--diagram-imports` to also diagram
classes referenced via `--ref` (docgen's existing "resolve external-term
definitions" flag) -- the same files double as "here's what an external
class's own triples look like" for this purpose, so no new input is
needed, just the flag.

## Running it

```
ontology-suite docgen --ontology examples/ontology/domain.ttl --prefix ex --out-dir out/docgen
```

```
Wrote out/docgen/ontology_doc_data.json: prefix='ex', 12 classes, 2 object properties, 4 datatype properties, 1 sections, 0 external terms (0 resolved).
Wrote out/docgen/ontology-documentation.html (12 classes, 2 object properties, 4 datatype properties, 0 external terms).
Reference documentation written to: out/docgen/ontology-documentation.html
12 class diagram(s) written to: out/docgen/class-diagrams
```

(paths above use `/` for readability; on Windows they print with `\`, same
as every other path this suite writes.)

`out/docgen/class-diagrams/` now has, per class, `<Name>.dot`,
`<Name>.svg`, `<Name>.png`, `<Name>.ttl` -- e.g. `Mammal.svg`/`Mammal.ttl`
for `ex:Mammal`. Open `ontology-documentation.html`: each class's card has
a "Class diagram" panel showing the SVG inline, with links to the SVG/PNG/
Turtle files alongside it.

Flags (on both `ontology-suite docgen` and `ontology-suite run --docgen`):

- `--no-class-diagrams` -- skip generation entirely (still just the
  HTML+JSON pair, same as before this feature existed).
- `--diagram-imports` -- also diagram external classes resolved via `--ref`.

If Graphviz's `dot` isn't on `PATH`, generation still writes `.dot`/`.ttl`
(no rendering needed for those) but skips `.svg`/`.png`, with a warning --
same graceful-degradation pattern as `oxi-gen`/`owlready2` elsewhere in
this suite, not a hard failure.

## What a diagram shows

Each class's diagram is its **concise bounded description (CBD)**: every
triple it's the subject of, plus -- recursively -- every triple any
blank-node object of those leads to (an `owl:Restriction`, a `unionOf`/
`intersectionOf` list cell, ...). A named-node object (a superclass, the
property a restriction is `owl:onProperty`, ...) appears as that edge's
endpoint but is **not** expanded past the one hop -- so a local class
diagrammed against an imported ontology shows its relationship to e.g. a
`gist:` superclass without pulling in gist's own full definition of that
superclass too. `docgen.class_diagrams.concise_bounded_description` is the
function; `tests/test_class_diagrams.py` has both a "recurses into blank
nodes" and a "does not expand past named nodes" regression test.

Rendering reuses `sketch.dot_export` -- the same renderer
`pattern_consistency`'s consistency-gap diagrams use (see
`docs/MODELLING_PATTERN_CONSISTENCY.md`'s "Visualising a gap" section for
the full colour/shape legend) -- so every diagram this suite produces
shares one visual language. There's no red/green consistency signal here
though (a class diagram isn't checking anything against anything else,
it's just showing one class's own definition), so ordinary IRI nodes and
edges use the plain, unhighlighted default (black nodes, gray edges).

**Blank nodes, lists, and literals get their own distinct styling**,
since `owl:Restriction`/`unionOf`/`intersectionOf`/`disjointWith` blank
nodes and datatype-property literal values are exactly what a class's own
CBD is full of:

- A plain blank node (a restriction, say) renders as a small unlabelled
  **amber point** (`sketch.dot_export.BLANK_NODE_COLOR`, `#C97A2B` --
  reusing the exact colour docgen's own HTML template already uses for
  "hint, not an axiom" styling, see `--amber` in
  `templates/documentation-template.html`), not a box with its raw
  `_:id` in it -- the id is unstable across parses anyway (rdflib mints a
  fresh one every time -- the same root cause `LOG-004` in `checks/` had
  to work around), and a blank node is structurally just a joint
  connecting real content, not an entity worth naming or colouring like a
  named one. Matches `create_class_diagrams.py`'s own `shape="point"`
  convention for the "no identifier" part (see the review below).
- An `rdf:first`/`rdf:rest` list chain -- what `owl:unionOf`/
  `intersectionOf`/`disjointWith`'s Turtle `( a b c )` syntax actually
  parses to -- compacts into **one** record-shaped node with a port per
  member, instead of one point node and two edges (`rdf:first`,
  `rdf:rest`) per list cell. A three-member union used to be a six-node
  zigzag that obscured the one thing worth seeing (what the union's
  members are); now it's one small box with three arrows fanning out.
  Nested lists (a union containing another list) nest correctly. Members
  still go through the normal node pipeline -- a literal member is still
  an ellipse, an IRI member is still a box -- the record is just a
  fan-out junction, not a dead end. A list cell *is* a blank node, so it
  gets the same amber -- plus rounded corners, since `shape=record`
  supports `style=rounded` too -- reinforcing "syntactic construct, not a
  modelled entity" for the same reason as the plain-point case above.
- A literal renders in its ellipse "container" with a **blue border**
  (`sketch.dot_export.LITERAL_BORDER_COLOR`, matching
  `turtle-editor-viewer`'s own literal convention) -- text stays the
  default black, so an explicit `node_colors` override (red/green
  consistency status, for a `pattern_consistency` diagram) still reads
  clearly against it rather than competing with a coloured label.

A class's own `{class} a owl:Class` triple never renders as an edge
either -- it's the one piece of the CBD guaranteed to be true of *every*
class diagrammed this way (that's the whole reason it's being diagrammed),
so showing it is pure repetition, not information. `{class}
rdfs:isDefinedBy <...>` is dropped the same way -- which ontology a term
belongs to is already the diagram's entire context (it's *this*
ontology's documentation), whatever that ontology's IRI happens to be.
Every *other* `rdf:type`/`rdfs:isDefinedBy` triple in the CBD (an
`owl:Restriction` blank node's own type, a different resource's
`isDefinedBy`, ...) still renders normally -- only these two specific
triples about the diagrammed class itself are suppressed, and only from
the *picture*: the `.ttl` export is the full, unedited CBD, so it still
has both (`docgen.class_diagrams._trim_for_diagram`,
`tests/test_class_diagrams.py::test_generated_dot_omits_a_owl_class_and_isdefinedby_but_ttl_keeps_both`).

All of this (points instead of dashed boxes, amber for every blank node,
list compaction, blue-bordered literals, `a owl:Class`/`rdfs:isDefinedBy`
suppression) came from direct feedback on how the diagrams actually
looked in practice once real ones were generated and rendered; see
`tests/test_dot_export.py`'s tests for worked examples (including a
nested `owl:unionOf`), and `docs/MODELLING_PATTERN_CONSISTENCY.md`'s
"Visualising a gap" section for how the same rendering looks in a
consistency-gap diagram.

## Collapsed by default

Each class's "Class diagram" panel in the HTML output is a `<details>`
element, collapsed by default (click "Class diagram" to expand) -- an
ontology with a hundred classes would otherwise open the page with a
hundred SVGs rendered at once. The `<img>` itself is `loading="lazy"`
too, so a collapsed diagram's SVG isn't even fetched until its panel is
opened.

## Python API

```python
from ontology_suite import pipeline

stage = pipeline.run_docgen_stage(
    "path/to/ontology.ttl", "out/docgen",
    prefix="ex",
    class_diagrams=True,       # default
    diagram_imports=False,     # default
)
stage.artifacts["class_diagrams"]  # {class_curie: ClassDiagram(dot_path, ttl_path, svg_path, png_path)}
```

Or call `docgen.class_diagrams` directly against an already-parsed graph
and an already-extracted `doc_data` dict (what `run_docgen_stage` does
internally) if you want diagrams without regenerating the HTML:

```python
from ontology_suite.docgen import class_diagrams as cd

generated = cd.generate_class_diagrams(graph, doc_data, out_dir / "class-diagrams")
cd.patch_doc_data_with_diagrams(doc_data, generated, out_dir)  # adds doc_data[...]["diagram"] paths
```

## Where this came from: reviewing `create_class_diagrams.py`

This feature was built after being shown a similar script from another
project (`oandt-sandbox`'s `create_class_diagrams.py`, owlready2 + a
hand-rolled DOT generator, one PNG + one `.ttl` per class via a `DESCRIBE
<class>` SPARQL query). It's a reasonable, working approach, and its
blank-node/literal handling and per-class CBD-via-DESCRIBE idea are
directly reflected in `concise_bounded_description` above (rdflib's own
`DESCRIBE` query answers exactly that algorithm, so `class_diagrams.py`
computes it directly rather than round-tripping through SPARQL for it).
Worth knowing about if you're comparing the two, or maintaining that
script separately:

- **A real bug**: `create_class_image()`'s call to `visualize()` (which
  does the actual DOT-generation + `dot` subprocess render) sits *inside*
  the `for ns, ns_uri in g.namespace_manager.namespaces(): ...` loop that
  binds namespaces onto the drawing graph -- so for a class in an ontology
  with, say, 15 declared prefixes, the exact same diagram gets
  regenerated and re-rendered 15 times per class. Almost certainly an
  indentation slip (the bind step belongs in that loop; the single
  `visualize()` call after it does not).
- **No local-vs-imported distinction**: `ccd.onto.classes()` (owlready2)
  returns every class visible in the loaded ontology, which -- because
  owlready2's `.load()` resolves `owl:imports` transitively by default --
  includes every imported ontology's classes too. Running it against a
  domain ontology importing gist would generate a diagram for all ~96 of
  gist's own classes as well as the domain's own, every single run. This
  is exactly the behaviour `class_diagrams.py` defaults away from
  (`include_external=False`), per this repo's `docs/UPDATING.md` framing
  of "local" vs. imported vocabulary as a boundary worth respecting
  deliberately rather than accidentally.
- **`shell=True` on a subprocess argument list** (`subprocess.check_call(["dot", ...], shell=True)`)
  is an unusual combination -- `shell=True` is normally paired with a
  single command *string*, not an argument list; it happens to work on
  Windows (`CreateProcess` via `cmd.exe`) but isn't the portable,
  recommended pattern. `class_diagrams.py` uses `subprocess.run([...],
  check=True, capture_output=True)` with no shell involved, matching how
  this suite's other subprocess call (`triplify/oxigen.py`'s own `dot`-
  adjacent `oxi-gen` invocation) already does it.
- **Dead code**: a `color()` method is defined (URIRef->black,
  BNode->orange, Literal->blue) but never called -- `declareTerm()` has
  its own separate, only-partially-overlapping colour logic inline
  instead (no colour attribute at all for a plain URIRef node).
- **Update**: the script's `renderList`/`isListNode` RDF-list-as-`record`-
  shape idea *was* ported after all (see "What a diagram shows" above) --
  originally left out as added complexity not worth it yet, but a real
  readability win for `unionOf`/`disjointWith` lists once actually asked
  for. `sketch.dot_export` determines list roots by absence of an
  incoming `rdf:rest` from another list cell, rather than "whichever cell
  a traversal happens to reach first" (the script's own approach, which
  depends on dict/set iteration order and can start mid-chain).
- One thing still **not** ported: writing the whole ontology's
  declared-namespace bindings onto every single class's tiny CBD graph.
  Harmless in the original, just unnecessary for a single-class diagram.
