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
4. **A Python *supplement* to a portable check** --
   `checks/literal_typing.py` is the only one, and it exists because
   `DAT-001`'s question ("is this literal valid for its datatype?") is one
   SPARQL cannot ask: there is no such operator, so both portable
   formulations approximate it with a regex over the lexical form. Under
   rdflib that regex cannot see an invalid `xsd:boolean` at all -- rdflib
   rewrites `"yes"^^xsd:boolean` to `'false'` on parse, which the regex then
   accepts -- and it cannot express value-space impossibilities like
   `"2021-02-30"^^xsd:date`. The supplement emits ordinary
   `sh:ValidationResult` triples tagged `oq:DAT-001` and merges through the
   same dedup key as everything else, so a literal both it and the regex
   catch stays one row with `literal-typing` added to `sources`. It runs
   under every `--engine` value on purpose: the gap is in the check
   formulations, not in one engine, and `--engine` is not supposed to change
   which findings exist.

See `docs/EXTENDING.md` for exactly how to add a check of any of these three
kinds.

### Graph walks run on the heap, not the call stack

`ontology_suite/hierarchy.py` holds the shared `ancestors` / `depth` /
`descendants_inclusive` walks over a class hierarchy. They are iterative,
over an explicit stack, and that is deliberate.

Four modules -- `ontologyeval`, `sketch`, `dataquality`, and
`versioning/diff.py` -- each carried their own recursive copy, and all four
guarded *cycles* but not *descent*. Hierarchy depth follows the longest
`rdfs:subClassOf` chain in the input, one Python frame per link, against
CPython's default 1000-frame limit. Measured end to end:
`ontologyeval.compute_metrics` raised `RecursionError` at a chain depth of
900, `sketch.ontology_quality.compute_metrics` at 5,000 -- from what is only
a metrics report. Called directly, `dataquality._ancestors` and
`versioning.diff._descendants_inclusive` both went at 1,000.

Those thresholds differ even between modules running structurally identical
code, and that is the tell: memoisation hides the descent whenever classes
happen to be visited shallowest-first, so how deep a document can go before
it throws depends on the order its classes were declared in. Declaring the
same chain the other way round -- just as valid a document -- was enough to
trigger it.

The blank-node walks in `reasoning/sampling.py` (Concise Bounded
Description) and `reasoning/profile.py` had the same shape for a different
reason: an RDF collection is a *chain* of blank nodes, one `rdf:rest` cell
per member, so depth there is set by the longest list in the input, not by
nesting. A 5,000-item `owl:oneOf` was enough. Both are now iterative;
`docgen/class_diagrams.py`'s own CBD already was.

Two fixes were considered and rejected, and the reasoning is worth keeping
because it generalises:

- **`sys.setrecursionlimit(N)`** does not grow the actual C stack, so a
  limit high enough to cover a deep chain trades a catchable
  `RecursionError` for an uncatchable interpreter crash. Strictly worse:
  the failure stops being a traceback that names the input.
  `tests/test_hierarchy.py` fails if any module reaches for it.
- **A depth cap.** Any limit low enough to be safe under a 1000-frame
  ceiling is also low enough to silently truncate a real answer.

Walking on the heap removes the ceiling instead of choosing one: `depth`
now reports 100,000 for a 100,000-link chain. `ancestors` and
`descendants_inclusive` return a set per node, so their cost is quadratic
in depth -- inherent to what they return, not to the walk, and the
recursive form simply crashed before anyone could notice. Cycle handling,
memo contents and return values are unchanged, verified against the
recursive originals over random cyclic and acyclic graphs in every visit
order.

**What is deliberately still recursive.** Eight self-recursive functions
remain, and the distinction that matters is *length* versus *nesting*.
Length is set by the data and had to go: a 5,000-item list, a 100,000-link
subclass chain. Nesting is set by how an expression is written, and stays
shallow in any document a person or a generator actually produces. So
`docgen/turtle_parser.py`, `docgen/extract_ontology_data.py` and
`sketch/dot_export.py` all walk list *cells* iteratively -- verified against
a 5,000-member `owl:unionOf` -- and recurse only per level of
expression nesting, where they overflow somewhere past 400. Nobody writes
400 nested `owl:complementOf`. `checks/registry.py`'s shape walk and
`checks/merge.py`'s path-expression renderer are bounded by this repo's own
shapes (one level of `sh:property`) and by an explicit depth guard
respectively.

The same argument, in the same week, produced `shacl` 0.1.10 upstream --
see the `--engine` section below for why that one does not change anything
here.

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
same real findings as pyshacl-backed `--engine both`, just without the
drift-detection signal, in roughly a seventh of the time. Reach for
`--engine sparql` when iterating quickly and the drift signal doesn't
matter (a suite-maintenance concern, not something that affects a user's
own ontology).

There's a third option that gets both the speed *and* the drift-detection
signal: `--engine native`/`--engine native+sparql`, which run the optional
native (Rust) SHACL engine (`checks/shacl_native_runner.py`,
https://github.com/pwin/SHACL_Engine) instead of pyshacl. Measured on the
exact same ~3,300-triple fixture used above, against `shacl` v0.1.3:
**0.29s**, vs. pyshacl's ~193s -- a ~665x speedup, with identical findings
*by identity* -- same 38 `(check_id, focus_node, value)` triples, same check
ids, zero discrepancies either direction; see `tests/test_shacl_native_runner.py`.
A larger, denser stress fixture (`examples/checks_stress_test/`,
`tests/test_engine_parity_stress.py`) confirms the same full parity at
scale for all 18 checks that have both a SHACL and SPARQL formulation,
including two cases (`STY-003`, many blank-node focus nodes at once;
`STY-001`/`STY-002`, blank-node-typed anonymous class/property
expressions) that each found a real native-engine bug, since fixed in
`shacl` 0.1.4 and 0.1.5 respectively -- see that test module's docstring
for the history. Re-verified at `shacl` 0.1.10: 0.1.6-0.1.10 added SHACL-AF
`sh:rule` support and a recursion-guard performance fix, neither of which
touches the `sh:sparql`/SELECT constraint path this suite's own checks
use -- full parity holds.

**Severity is part of parity too, since the shapes stopped declaring it in
the wrong place.** SHACL defines `sh:severity` as a property of a *shape*.
Every shape here used to declare it one level down, on the
`sh:SPARQLConstraint` blank node inside its `sh:sparql [ ... ]` block --
legal Turtle that no processor is obliged to read, and pyshacl does not: it
fell back to the spec default and reported *every* SHACL-sourced finding as
`Violation`. The native engine did read the nested form, so the two engines
returned different severities for identical findings:
`examples/ontology/domain.ttl`'s 18 findings came back as 17 Violation/1
Warning/0 Info under `--engine shacl` and 1 Violation/15 Warning/2 Info
under `--engine native`.

That mattered because every subcommand defaults to `--fail-on Violation`:
the same ontology passed or failed a CI gate purely on which `--engine` was
passed, and a class named `person_record` (`STY-001`, registry default
`Warning`) failed exactly as hard as a logical contradiction. Moving each
declaration onto its enclosing shape fixed both engines at once -- both now
report 1 Violation/15 Warning/2 Info, matching `registry.json` exactly.
(`sh:severity` on a SHACL-core property shape -- e.g. `LOG-001`'s
`sh:property [...]` -- was always fine; a property shape *is* a shape.)

Guarded by `tests/test_shape_severity.py` (the placement itself, and each
shape against its registry `default_severity`) and
`tests/test_shacl_native_runner.py::test_every_severity_matches_the_registry_under_both_engines`
(the observable behavior, under both engines). `_row_key` in that module
now includes severity in the identity comparison, where it used to exclude
it precisely because of this divergence.

**Fixed history: blank-node focus nodes used to cross-join under the
native engine.** Before `shacl` 0.1.4, a `sh:sparql`-formulated check
whose target could include blank-node focus nodes (in this suite's own
shapes, only `STY-003`'s `sh:targetSubjectsOf rdfs:label`) didn't scope
`$this` per focus node correctly: N blank-node focus nodes sharing the
shape produced N² findings instead of N. Reported upstream and fixed in
`shacl` 0.1.4 (https://github.com/pwin/SHACL_Engine); confirmed fixed here
at N=2/3/4/60 -- `tests/test_engine_parity_stress.py` still carries a
regression test for it, so a pre-0.1.4 wheel ever getting reinstalled
(see the `uv sync` note below) is caught rather than silently corrupting
results.

**Fixed history: blank-node focus nodes used to leak past `isIRI($this)`.**
Found immediately after installing `shacl` 0.1.4 to verify the N²
cross-product fix above -- turned out to be a regression from (or a
boundary case left uncovered by) that same fix, since both involve the
same per-focus-node `$this`-scoping machinery. `FILTER(isIRI($this))`
inside a `sh:sparql` constraint stopped excluding blank-node focus nodes
in `shacl` 0.1.4: `STY-001` (`sh:targetClass owl:Class`) and `STY-002`
(`sh:targetClass owl:ObjectProperty`/`owl:DatatypeProperty`) both rely on
exactly this filter to exclude blank-node-typed anonymous class/property
expressions (`owl:unionOf`/`owl:intersectionOf` members, `owl:inverseOf`'s
anonymous object, etc. -- extremely common in real ontologies) from a
check that's only meaningful for a real, named local name. Confirmed
against real data: gist 14.1.0 alone has ~97 such blank nodes, and
`STY-001` reported every one of them as a false positive under `--engine
native`/`native+sparql` under 0.1.4, where pyshacl correctly found none.
Reported upstream and fixed in `shacl` 0.1.5 -- confirmed fixed here at
the same scale (real gist data, 56/56 full parity) -- see
`tests/test_engine_parity_stress.py::test_native_isiri_this_blank_node_regression`
and `tests/test_vehicle_gist_checks.py::test_native_engine_matches_pyshacl_on_the_real_vehicle_gist_fixture`,
which still carry regression tests for this.

`--engine native+sparql` is therefore strictly better than `--engine both`
when the optional `shacl` package is available: same cross-validation
value, at roughly `--engine sparql` speed -- which is why the CLI's
`--engine` default (`pipeline.default_engine()`) auto-selects
`native+sparql` when the package is importable and falls back to `both`
otherwise, rather than making everyone type the flag by hand. (Library
callers going through `pipeline.run_registry_suite_on_graph`/
`run_checks_stage`/`run_data_stage` directly, not via the CLI, still
default to `"both"` explicitly -- `tests/test_vehicle_gist_checks.py`
pins an exact finding count against pyshacl specifically and needs that
default to stay environment-independent.) `shacl` is on PyPI as of 0.1.4
(0.1.5 for the isIRI($this) fix above -- this package's `native-shacl`
extra pins `shacl>=0.1.10`, kept current with upstream), installed via the
opt-in `uv sync --extra native-shacl` (same convention as this package's
own `reasoner` extra) -- see `shacl_native_runner.py`'s
module docstring for why its SHACL-core (non-SPARQL) findings need a
`sh:message`-text fallback to resolve a check id (blank node identifiers
don't survive the Rust/Python boundary the way pyshacl's own in-process
ones do).

**Plain `uv sync` (no `--extra native-shacl`) still uninstalls `shacl`**
-- standard behavior for any opt-in extra, easy to trip over: it silently
changes the CLI's default engine back to `both`/pyshacl until reinstalled
with `uv sync --extra native-shacl`. Since the severity fix above that is a
wall-clock change rather than a change in what gets reported, but a large
one -- pyshacl is the slow path.

`--inference rdfs` is supported under `--engine native`/`native+sparql` as
of `shacl` v0.1.3 (materialised into the data graph before validation,
same semantics as pyshacl's own RDFS option) -- `--inference owlrl`/`both`
raise a `ValueError` under the native engine rather than silently
downgrading, since it has no OWL2-RL reasoner (`reasoning/backends/
owlrl_backend.py`'s pure-Python OWL2-RL closure is deliberately not
swapped for it -- RDFS-only entailment would silently drop real findings
that depend on OWL2-RL-specific rules).

## Pipeline stages (`ontology_suite/pipeline.py`, driven by `cli.py`)

1. **`ontology`** -- the ontology as authored: `ontology_evaluation.py`'s
   OntoQA/OQuaRE metrics, OWL2 expressivity counts, documentation coverage,
   and lint flags (unchanged from TarqlVisualisation), plus two new things:
   `reasoning/profile.py` (OWL2 EL/QL/RL) and
   `reasoning/consistency.py` (owlrl-backed, optionally a real DL reasoner).
   Optionally also generates the `docgen` reference documentation page (see
   below) when `--docgen` is passed to `run`, or always when invoked
   directly as `ontology-quality-suite docgen`.
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
A real user ran `ontology-quality-suite run --ontology ... --import-dir ...
--allow-network` and still got a near-total false-positive flood, because
the flags never reached the stage producing most of the findings. Fixed by
centralizing import resolution in one helper every stage calls.

**An import that fails to resolve is reported, not just recorded.** The
consequence of a missing `owl:imports` is not subtle: every term the
absent ontology declares is undeclared as far as
`data_quality.check_conformance` can tell, so one unresolved import becomes
a flood of false `CNF-001`/`CNF-002` findings. That flood used to arrive
with no stated cause -- `resolve_imports` returned the unresolved list, but
only `--verbose` ever printed it. A second user report made the cost plain:
an ontology importing three of its four modules produced 30 conformance
warnings, 28 of them false, and diagnosing it meant knowing to re-run with
`--verbose`. `pipeline.import_warnings` now turns the report's problem
sections into `StageResult.warnings`, so they show in the default summary
and name the consequence (`... every term they declare will be reported
undeclared (CNF-001/CNF-002)`) rather than leaving the reader to connect
cause to symptom. Three cases qualify:

- **unresolved** -- no local candidate declared the imported IRI, and
  either `--allow-network` was off or the fetch failed.
- **unparsable** -- a candidate file could not be parsed at all. Previously
  a bare `except Exception: continue` erased these without trace.
- **ambiguous** -- more than one file under `--import-dir` declares the same
  ontology IRI (a stale copy beside a current one, a file beside its
  backup). The sort-first file still wins; what changed is that the
  collision is now named instead of decided in silence.

Identifying every candidate costs one parse per file in `--import-dir`,
where the older early-`break` scan stopped at the first match. For more
than one `owl:imports` this is a net saving -- the old scan restarted from
the top for each pending IRI -- and it is what makes ambiguity detection
possible at all. A single import inside a very large `--import-dir` is the
one case that pays more.

### Loading files: local, http(s), and gzip (`io_utils.py`)

Every loader in this suite -- `--ontology`, `--data`, `--old`/`--new` (
`consistency`/`version-diff`), `tarql_sources`/`ontology_paths` (
`sketch.prefix_alignment`, the `repair`/`consistency` modules), folders
resolved by `dataquality.data_quality.resolve_input_paths` -- accepts a
local file path, an http(s) URL, or either gzip-compressed, via the shared
helpers in `io_utils.py`. Two real gaps that module exists to close, found
by testing actual behavior rather than assuming it:

1. `pathlib.Path("https://example.org/foo.ttl")` collapses the `//` into a
   single `/` on Windows (`WindowsPath('https:/example.org/foo.ttl')`),
   silently turning a valid URL into a nonexistent local path.
   `checks/runner.py::load_graph` (the `checks` stage's `--data` loader)
   had exactly this bug -- `ontology-quality-suite checks --data <url>` failed
   outright. `io_utils` never wraps a source in `Path` before checking
   whether it's a URL.
2. `rdflib.Graph.parse()` does not sniff for gzip -- handing it
   gzip-compressed bytes raises `UnicodeDecodeError`. `io_utils` checks
   every read for the gzip magic bytes (not just a `.gz` suffix, so a
   server-side `Content-Encoding: gzip` response under a plain `.ttl` URL
   is also caught) and transparently decompresses.
3. **The file extension lies, and when it does the failure is silent.**
   `guess_format` maps `.owl` to `xml`, but Protege writes `.owl` files in
   Turtle by default; the RDF/XML parser then throws on the first
   `@prefix`. Reported by a user whose imports sat in `--import-dir` as
   exactly such files: every import came back unresolved and every term
   declared in them was flagged `CNF-001`/`CNF-002` against queries that
   were correct. `sniff_format` reads the opening bytes, and
   `resolve_format` lets the content override the extension -- but only
   across format *families* (`_FORMAT_FAMILIES`). A `.n3` file opening with
   `@prefix` sniffs as `turtle` and stays `n3`, because N3 is a superset
   and downgrading it would reject the syntax the extension promised; a
   sniff that finds nothing conclusive (N-Triples has no header) leaves the
   guess alone. Bytes are evidence, an extension is only a claim.

**`allow_network`'s asymmetry is deliberate, not a bug.** A source the
caller names *explicitly* -- `--ontology <url>`, an entry in
`tarql_sources`/`ontology_paths` -- is something the user already consented
to by typing it, so it's allowed by default, with no flag needed. A source
*discovered* while parsing other content is not something the user directly
asked for; the only case in this suite is an `owl:imports` target found
inside an ontology file, and that stays gated behind `--allow-network`
exactly as before -- `ontology_evaluation.resolve_imports` is the only
caller that ever passes `allow_network=False` to `io_utils`. Repair
suggestions (`repair/tarql_repair.py`) are the one deliberate exception:
they write a patch back to the file they read, so they read with a plain
`Path(...).read_text()`, not `io_utils` -- a URL source there would have
nothing sensible to write the fix back to.

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
`--docgen` to also generate it there, or run `ontology-quality-suite docgen`
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
