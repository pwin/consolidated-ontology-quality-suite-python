# Architecture

## What this suite is

Three sibling projects each solved one slice of "triplify CSV via
TARQL/oxi-gen-style SPARQL `CONSTRUCT` queries, against an OWL2 ontology,
then assess the result":

- **`oxi-gen`** (Rust, referenced as a sibling repo, not vendored) -- the
  actual triplifier. CSV + a SPARQL `CONSTRUCT` query (tarql-compatible
  syntax) -> Turtle/N-Triples. Invoked as an external process by
  `ontology_suite/triplify/oxigen.py`; see "Why oxi-gen isn't vendored"
  below.
- **`ontology-quality-suite`** -- a registry-driven SHACL+SPARQL check
  suite (`registry.json`, `shapes/`, `sparql/<category>/*.rq`), ported here
  as `ontology_suite/checks/` and `ontology_suite/report/`.
- **`TarqlVisualisation`** -- five scripts ported here as
  `ontology_suite/sketch/` (`tarql_visualiser.py`, `graph_quality.py`,
  `ontology_quality.py`), `ontology_suite/dataquality/` (`data_quality.py`),
  and `ontology_suite/ontologyeval/` (`ontology_evaluation.py`).

None of the three did real logical/consistency reasoning or OWL2 profile
(EL/QL/RL) classification -- that gap is filled by `ontology_suite/reasoning/`,
genuinely new in this suite. `ontology_suite/versioning/` (semver-style
ontology version diffing) is also new, as is `ontology_suite/docgen/` --
ported from a fourth, separate project (`docgen3`): generates a
human-readable HTML reference documentation page for an ontology (class/
property tables, Mermaid class-hierarchy/object-property diagrams,
external-vocabulary cross-references), distinct in kind from everything
above -- it's reference documentation, not a quality assessment, and
produces no `ResultRow` findings at all, only an HTML artifact. See
"Reference documentation (`docgen`)" below.

## The unifying idea (inherited from ontology-quality-suite)

Every check -- whether SHACL, a portable SPARQL `CONSTRUCT`, or now a
Python-native computation (OWL2 profile membership, reasoner output,
ontology-conformance diffing, semver classification) -- ultimately becomes a
`ResultRow` (`ontology_suite/checks/merge.py`), the same shape pyshacl's own
`sh:ValidationResult` implies:

```
check_id, category, title, severity, focus_node, path, value, message, remediation, sources
```

The report layer (`ontology_suite/report/*.py`) only ever reads this shape.
It has no SHACL-only, SPARQL-only, *or* stage-only code path -- a finding
from the `ontology` pipeline stage, the `checks` stage, or the reasoning
layer renders in exactly the same tables/plots/HTML/Cucumber JSON. This is
why adding `reasoning`/`conformance` as brand-new registry categories
required zero changes to `report/tables.py`, `plots.py`, `cucumber.py`, or
`html_report.py` -- see `docs/EXTENDING.md`.

`registry.json` is still the single source of truth for every check's id,
category, severity, and remediation text, shared by:

1. **Standalone SPARQL `CONSTRUCT` tests** (`sparql/<category>/<id>.rq`) --
   bind `sh:sourceConstraintComponent` directly to `oq:<id>`. This is the
   portable path: it needs nothing but a SPARQL 1.1 engine.
2. **SHACL shapes** (`shapes/<category>.ttl`) -- named `oq:<id>` directly,
   or annotated `oq:checkId "<id>"` when a check needed several NodeShapes.
3. **Native Python findings** -- `reasoning/profile.py`,
   `reasoning/backends/*.py`, and `dataquality/data_quality.py`'s
   `conformance_to_rows` construct `ResultRow`s directly (no SHACL/SPARQL
   round-trip), tagged with the same registry ids. This is the one thing the
   original architecture doc didn't anticipate: not every useful check is a
   graph pattern over a single merged graph -- OWL2 profile membership is a
   syntactic classification, and a real DL reasoner's output isn't RDF at
   all until this suite shapes it into a `ResultRow`.

See `docs/EXTENDING.md` for exactly how to add a check of any of these three
kinds.

### Choosing which engine actually runs (`--engine`)

Running both the SHACL and SPARQL formulation of every check is valuable
for catching drift between them, but it isn't free: measured against a
real ~3,300-triple ontology (the `examples/vehicle/` + gist fixture),
pyshacl takes **~193s** where the portable SPARQL layer alone takes **~27s**
for the same ~50-check pass -- pyshacl re-derives, via its own Python-level
shape traversal and constraint dispatch, findings the SPARQL layer already
produces directly by running the identical query.

At the time of writing, every check implemented in SHACL also has a
portable SPARQL twin -- there is no check unique to SHACL
(`tests/test_check_coverage.py` asserts this stays true). That means
`--engine sparql` (on `checks`, `data`, and `run`; see
`ontology_suite/pipeline.py::run_registry_suite_on_graph`) finds the exact
same real findings as the default `--engine both`, just without the
drift-detection signal, in roughly a seventh of the time. `--engine both`
remains the default -- reach for `--engine sparql` when iterating quickly
or running in CI where speed matters more than catching a SHACL/SPARQL
formulation drifting apart from itself (a suite-maintenance concern, not
something that affects a user's own ontology).

## Pipeline stages (`ontology_suite/pipeline.py`, driven by `cli.py`)

1. **`ontology`** -- the ontology as authored: `ontology_evaluation.py`'s
   OntoQA/OQuaRE metrics, OWL2 expressivity counts, documentation coverage,
   and lint flags (unchanged from TarqlVisualisation), plus two new things:
   `reasoning/profile.py` (OWL2 EL/QL/RL) and
   `reasoning/consistency.py` (owlrl-backed, optionally a real DL reasoner).
   Optionally also generates the `docgen` reference documentation page (see
   below) when `--docgen` is passed to `run`, or always when invoked
   directly as `ontology-suite docgen`.
2. **`checks`** -- the registry-driven SHACL+SPARQL suite
   (`checks/runner.py`), against the ontology and/or a data graph.
3. **`sketch`** -- `tarql_visualiser.py` builds a Turtle "sketch" of the
   graph shape a folder of oxi-gen/tarql `CONSTRUCT` queries would build (no
   CSV needed); `graph_quality.py`/`ontology_quality.py` analyse it as
   before. New: if an ontology is given, the sketch is run back through
   `data_quality.py`'s own `ontology_declarations` + `check_conformance` --
   the exact same conformance logic the `data` stage uses against real
   triplified output -- to diff the query shape against what the ontology
   actually declares (`CNF-00x` findings, category `conformance`).
4. **`triplify`** -- `triplify/oxigen.py` shells out to a built `oxi-gen`
   binary per CSV/query pair (paired by `triplify/discovery.py`'s naming
   convention) to produce real Turtle data.
5. **`data`** -- `data_quality.py`'s real-data-vs-ontology conformance
   report (same `check_conformance` as above, tagged `"data"` instead of
   `"sketch"`), the registry suite, and the reasoning layer -- optionally
   over a `reasoning/sampling.py` Concise-Bounded-Description sample of the
   data graph's named subjects, so the expensive reasoning pass stays
   tractable on large exports while the cheap SPARQL/SHACL checks still run
   over the whole thing.
6. **`run`** -- runs whichever stages apply given the inputs, into one
   `--out-dir`, then merges every stage's `ResultRow`s into one
   `report.html`/`full_results.csv`/`cucumber.json`.

`version-diff` is a separate, standalone subcommand (`ontology_suite/versioning/`)
-- it compares two ontology *files*, not a data pipeline stage, so it isn't
part of `run`. See `docs/VERSIONING.md`.

Every stage that takes `--ontology` (`ontology`, `checks`, `sketch`, `data`,
and `run`'s calls into each of them) resolves `owl:imports` the same way,
via the shared `pipeline.load_ontology_graph` helper -- transitively,
local-first, network only with `--allow-network`. This wasn't always true:
`checks`/`sketch`/`data` originally each loaded the ontology file alone
with a plain parse, no import resolution at all, and `run`'s own calls into
those stages didn't forward `--import-dir`/`--exclude-imports`/
`--allow-network` even though the separate `ontology` stage honored them.
A real user ran `ontology-suite run --ontology ... --import-dir ...
--allow-network` and still got a near-total false-positive flood, because
the flags never reached the stage producing most of the findings. Fixed by
centralizing import resolution in one helper every stage calls.

## The reasoning layer

- **`reasoning/profile.py`** -- a heuristic, syntactic OWL2 EL/QL/RL
  profile-membership checker (no reasoner needed). See `docs/REASONING.md`
  for exactly what it checks and its limits.
- **`reasoning/backends/owlrl_backend.py`** -- always available (pure
  Python, `owlrl` package): materializes an RDFS/OWL2-RL closure, then
  reruns `sparql/logical/*.rq` and the new `sparql/reasoning/REA-00x.rq`
  contradiction patterns against the *closed* graph, so entailed
  contradictions surface (e.g. an individual's disjoint-class membership
  that's only implied via a subclass chain), not just directly-asserted
  ones. Sound but not complete for full OWL2 DL.
- **`reasoning/backends/external_backend.py`** -- best-effort, optional
  (the `reasoner` extra: `owlready2`, which itself shells out to a Java
  HermiT/Pellet process). Complete for full OWL2 DL when available; reports
  a clean `REA-022` "unavailable" finding rather than failing when it isn't.
- **`reasoning/consistency.py`** -- dispatches to both, merges the result.
- **`reasoning/sampling.py`** -- Concise Bounded Description sampling for
  the `data` stage's `--sample N` flag.

## Reference documentation (`docgen`)

`ontology_suite/docgen/` is ported from a fourth, separate project
(`docgen3`) that has nothing to do with SHACL/SPARQL findings -- it's an
ontology *reference manual* generator:

- **`extract_ontology_data.py`** -- walks the ontology graph (via
  `turtle_parser.py`, a thin rdflib adapter -- see its docstring) and
  produces a JSON data model: classes, object/datatype properties (with
  domain/range and, separately, gist-style `domainIncludes`/`rangeIncludes`
  annotations, never merged with real OWL axioms since that would
  misrepresent an annotation hint as a logical one), comment-header
  sections, and external-vocabulary cross-references (resolved against
  `--ref` files if given). Correctly renders anonymous class expressions
  (`owl:Restriction`, `unionOf`/`intersectionOf`/`complementOf`, at
  arbitrary nesting) as readable text rather than raw blank-node ids.
- **`build_documentation.py`** -- injects that JSON into
  `templates/documentation-template.html` (Handlebars.js + Mermaid.js,
  loaded from CDN, rendering entirely in-browser), producing one
  self-contained `ontology-documentation.html`.
- **`class_diagrams.py`** -- one Graphviz `.dot`/`.svg`/`.png` diagram plus
  a `.ttl` of its own concise bounded description, per class -- unlike the
  three Mermaid diagrams above (whole-ontology, computed client-side from
  the flat JSON arrays), this runs at build time and writes real files
  under `class-diagrams/`, reusing `sketch.dot_export`'s rendering (the
  same one `pattern_consistency`'s gap diagrams use, so every diagram this
  suite produces shares one visual language). Local classes only by
  default -- `--diagram-imports` opts into diagramming external classes
  resolved via `--ref` too. See `docs/CLASS_DIAGRAMS.md`.

This is deliberately **not** part of `run`'s default output -- pass
`--docgen` to also generate it there, or run `ontology-suite docgen`
directly. Only the HTML/JSON pair produces no `ResultRow` findings; the
class-diagram files are pure artifacts too (kept alongside the JSON so the
render step can be re-run alone after hand-editing the template, without
re-parsing the ontology or regenerating diagrams). Validated against real
gist 14.1.0 (96 classes / 66 object properties / 50 datatype properties,
matching `docgen3`'s own documented benchmark exactly --
`tests/test_docgen.py`).

## Why oxi-gen isn't vendored

`oxi-gen` is a mature, independently-versioned Rust binary with its own
build (`cargo build --release`) and test suite. Vendoring its source here
would mean tracking a second copy that drifts from the original. Instead,
`ontology_suite/config.py::find_oxi_gen_binary` looks for a built binary at
the sibling `../oxi-gen/target/release/oxi_gen(.exe)`, an
`ONTOLOGY_SUITE_OXI_GEN_BIN` env var, an explicit `--oxi-gen-bin` flag, or
`PATH` -- in that order -- and `triplify/oxigen.py` shells out to whichever
it finds. The same pattern applies to `turtle-editor-viewer` (a companion
React/TypeScript Turtle editor and SPARQL-in-browser tool): it's referenced,
not vendored, and the hosted instance at
`https://semantechs.co.uk/turtle-editor-viewer-new/` is linked directly from
`report/html_report.py`'s "Artifacts" section next to every `.ttl` file a
stage produced, with no local build step required to use it.

## Why the Rust check-runner (`oq-lint`) was dropped

`ontology-quality-suite` shipped a second, Rust-only runner that
re-executes the portable SPARQL layer via oxigraph, as a lighter-weight
alternative to pyshacl. This suite carries forward only the Python
framework: maintaining two check engines wasn't worth it once `oxi-gen`
already covers this project's Rust component (triplification, where
performance actually matters for large CSVs). The portable `.rq` checks
remain plain SPARQL 1.1, so nothing stops a future contributor from adding
a Rust runner back if it's ever needed -- see `docs/EXTENDING.md`.

## Aggregate / graph-wide checks and the synthetic focus node

A few checks (`EFF-002`, `QUA-005`) describe a property of the whole graph
rather than of one resource. These report against a synthetic anchor IRI,
`oq:Graph`, rather than a real resource -- called out in the check's
description in `registry.json`/`docs/CHECKS.md`.
