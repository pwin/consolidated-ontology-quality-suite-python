# Consolidated Ontology Suite

Describes and assesses the RDF triplification process built on OWL2
DL/RL/QL ontologies: ontology quality and expressiveness, TARQL/oxi-gen
`CONSTRUCT`-query graph-shape sketches, real triplified data quality, OWL2
profile membership, reasoner-backed consistency checking, and
semantic-versioning-style ontology comparison -- one unified CLI and
report layer over all of it.

Built by consolidating four sibling projects -- **`oxi-gen`** (the Rust
CSV-to-RDF triplifier), **`ontology-quality-suite`** (the registry-driven
SHACL+SPARQL check suite), **`TarqlVisualisation`** (OntoQA/OQuaRE-style
quality metrics), and **`docgen3`** (ontology reference-documentation
generation) -- plus a new reasoning layer and version-diff tool this suite
adds on top. See `docs/ARCHITECTURE.md` for the full picture of how the
pieces fit together and why.

## Install

```
uv sync                      # base install
uv sync --extra reasoner     # + owlready2, for real OWL2 DL reasoning (needs a Java runtime too)
uv sync --group dev          # + pytest, to run tests/
```

Triplification needs a built `oxi-gen` binary: `cd ../oxi-gen && cargo build --release`
(referenced as a sibling repo, not vendored -- see `docs/ARCHITECTURE.md`).

## Quick start

```
ontology-suite ontology  --ontology examples/ontology/domain.ttl
ontology-suite checks    --ontology examples/ontology/domain.ttl --data examples/data/legacy-export.ttl
ontology-suite sketch    --queries examples/queries --ontology examples/ontology/domain.ttl
ontology-suite triplify  --csv-dir examples/csv --queries examples/queries --out-dir out/data
ontology-suite data      examples/data/legacy-export.ttl out/data/*.ttl --ontology examples/ontology/domain.ttl
ontology-suite docgen    --ontology examples/ontology/domain.ttl
ontology-suite run       --ontology examples/ontology/domain.ttl --queries examples/queries \
                         --csv-dir examples/csv --data examples/data/legacy-export.ttl --docgen
ontology-suite version-diff examples/gist_versions_reference/gistCore14.0.0.ttl \
                            examples/gist_versions_reference/gistCore14.1.0.ttl
```

Every subcommand writes `report.html` (start here), `full_results.csv`,
per-category/per-check summaries, `cucumber.json`, and Gherkin
`features/*.feature` under `--out-dir`, via the same report layer
regardless of which stage produced the findings. `run` composes whichever
stages apply given the inputs and merges everything into one report.

`checks`/`data`/`run` also take `--engine {both,sparql,shacl}` (default
`both`). Every check has a portable SPARQL form; only some also have a
SHACL one, purely for cross-validation. `--engine sparql` skips pyshacl
entirely -- same real findings, roughly 7x faster (see
`docs/ARCHITECTURE.md`).

## What each piece does

| Command | Question it answers |
|---|---|
| `ontology` | Is the ontology itself well-designed? OntoQA/OQuaRE metrics, OWL2 expressivity, documentation coverage, lint flags, OWL2 EL/QL/RL profile membership, and reasoner-backed consistency -- no data involved. |
| `checks` | Does the ontology/data conform to the 50-check registry (structural integrity, logical cogency, documentation, efficiency, naming style, data quality)? |
| `sketch` | If I only have TARQL/oxi-gen `CONSTRUCT` queries (no CSV run yet), what graph shape would they build, and does that shape actually match the ontology's declarations? |
| `triplify` | Actually run the CSVs through `oxi-gen` to produce real RDF. |
| `data` | Now that I have real data, does it hold together, and does it conform to the ontology it's supposed to follow -- checked exactly, or against a sample for the reasoning pass if it's large? |
| `docgen` | Generate a human-readable reference documentation page for the ontology itself -- class/property tables, Mermaid diagrams, external-vocabulary cross-references. |
| `version-diff` | Comparing two versions of an ontology, what changed structurally, and is it a MAJOR, MINOR, or PATCH change? |

See `docs/ARCHITECTURE.md` (pipeline stages and design), `docs/CHECKS.md`
(the full 50-check catalogue, generated from `registry.json`),
`docs/REASONING.md` (OWL2 profiles and consistency checking in depth),
`docs/VERSIONING.md` (the version-diff heuristic, validated against real
gist release history), `docs/EXTENDING.md` (how to add a new check),
`docs/TARQL_ALIGNMENT.md` (checking a TARQL/oxi-gen query against an
ontology -- prefix/namespace drift, undeclared classes/properties, and
reviewing real triplified output -- especially after an ontology version
bump), `docs/MODELLING_PATTERN_CONSISTENCY.md` (finding where a modelling
pattern has drifted out of alignment across all four of ontology,
taxonomy, transformation, and triple output -- not just ontology vs.
transformation -- and a roadmap toward automating the fix),
`docs/CLASS_DIAGRAMS.md` (per-class `.svg`/`.png`/`.ttl` diagrams embedded
in `docgen`'s HTML output, local classes only by default), and
`docs/UPDATING.md` (a playbook for updating the ontology/taxonomy/data
ecosystem as one coordinated system -- entry points, sequencing, rollout
tooling, and governance).

## Companion tools (referenced, not vendored)

- **`oxi-gen`** (`../oxi-gen`) -- the triplifier itself; `config.py` finds
  its built binary automatically or via `--oxi-gen-bin`.
- **[turtle-editor-viewer](https://semantechs.co.uk/turtle-editor-viewer-new/)**
  -- a hosted Turtle/RDF editor with graph visualization and in-browser
  SPARQL querying. Every `.ttl` artifact a pipeline stage writes gets a
  direct link to it in `report.html`'s "Artifacts" section -- paste in or
  load the file to explore it interactively.

## Examples

`examples/` has a small hand-authored ontology + data + oxi-gen query/CSV
exercising every check category (including a deliberate post-closure
disjointness contradiction), and `examples/gist_versions_reference/` has
seven real, consecutively released versions of Semantic Arts' gist
ontology used to validate the version-diff tool against real release
history (see `docs/VERSIONING.md`).

## Tests

```
uv run pytest tests/
```
