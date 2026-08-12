# Primer: strategies and workflows for the Ontology Quality Suite

A task-oriented map of this suite, for someone deciding *how* to use it, not
just what each flag does (that's the other docs -- this one links out to
them at every section). Every command below is runnable as-is against this
repo's own `examples/` fixtures, **except** where a section has no matching
committed fixture (a genuine rename-with-migration-annotation across two
ontology versions, a live Fuseki endpoint) -- those are called out
explicitly, with illustrative filenames/URLs to substitute your own.

### New to RDF/OWL? A few terms this doc uses

- **Ontology** -- the schema: the classes ("Vehicle"), properties
  ("hasOwner"), and rules that define what your data is *allowed* to look
  like. (RDF/OWL jargon: the **TBox**, short for "terminology box" -- you'll
  see this term used once below and in a couple of the other docs.)
- **Data graph** -- the actual facts built using that schema: specific
  vehicles, specific owners. (Jargon: the **ABox**, "assertion box.")
- **Triple** -- one fact, written subject-predicate-object, e.g. "car1 is a
  Vehicle." A graph is just a big pile of these.
- **IRI** -- a globally unique name for something (a class, a property, a
  specific real-world thing), usually written as a URL (e.g.
  `https://example.org/Vehicle`). It doesn't need to be a real, loadable web
  page -- it's functioning as a name, not a link.
- **SHACL** and **SPARQL** -- two different languages this suite uses to
  express a "check." SPARQL asks "find me anything matching this pattern";
  SHACL says "data shaped like this is/isn't allowed." This suite writes
  most checks in both, independently, so the two can cross-check each other.
- **Reasoner** -- software that derives new facts from existing ones plus
  the ontology's rules (e.g. "X is a Car, every Car is a Vehicle, therefore
  X is a Vehicle") and can catch outright contradictions.
- **Taxonomy** -- a controlled, fixed list of allowed values (e.g. the only
  valid "Fuel Type" options), layered on top of the ontology's classes.
- **Blank node** -- a piece of RDF structure with no name/IRI of its own --
  used for local detail that doesn't need to be globally referenceable.

None of this is required reading to follow the rest of this doc -- it's
here so the occasional bit of jargon below has somewhere to point back to.

## 1. The shape of the suite, in one picture

Four layers, each building on the one before:

1. **Ontology-only assessment** (`ontology`) -- is the schema itself
   well-formed, documented, expressive in the way you intend, and logically
   consistent? No data involved.
2. **Registry-driven checks** (`checks`) -- the full 50-check catalogue
   (structural integrity, logical cogency, naming style, documentation,
   efficiency, data quality) against an ontology and/or a data graph.
3. **Data-pipeline stages** (`sketch`, `triplify`, `data`) -- validate a
   CSV-to-RDF query *before* running real data through it, run the
   triplifier, then assess what it actually produced.
4. **Cross-cutting layers**, each answering a question the layers above
   can't: has the ontology changed in a breaking way (`version-diff`), can
   drift be auto-repaired (`consistency`), does a taxonomy of controlled
   values stay in step too (`pattern-consistency`), does a *live*
   triplestore hold together (`consistency-remote`), and can a
   non-technical stakeholder read any of this (`docgen`).

`run` composes whichever of layers 1-3 apply given the inputs you pass it,
into one merged report -- the fast path once you know which stages you
need; the sections below are about *deciding* that.

## 2. Decision table: "I want to..." -> command

| You want to... | Run | Docs |
|---|---|---|
| Assess an ontology's own quality/expressivity/consistency | `ontology` | this doc §4 |
| Run the full registry (50 checks, 8 categories) against ontology and/or data | `checks` | `CHECKS.md` |
| Check TARQL/oxi-gen queries against the ontology, no CSV needed | `sketch` | `TARQL_ALIGNMENT.md` |
| Actually produce RDF from CSV | `triplify` | this doc §5 |
| Assess real triplified data (structure + conformance + reasoning) | `data` | this doc §5 |
| Generate human-readable reference documentation | `docgen` | `CLASS_DIAGRAMS.md` |
| Compare two ontology versions, get a MAJOR/MINOR/PATCH suggestion | `version-diff` | `VERSIONING.md` |
| Detect + auto-suggest repairs for ontology/TARQL drift | `consistency` | `CONSISTENCY_AND_REPAIR.md` |
| Check a taxonomy layer against ontology+transform+data | `pattern-consistency` | `MODELLING_PATTERN_CONSISTENCY.md` |
| Run checks against a *live* Fuseki dataset instead of files | `consistency-remote` + `remote.fuseki`/`remote.manifest` (Python) | `FUSEKI.md` |
| Run everything that applies, one report | `run` | `ARCHITECTURE.md` |
| Add a new check | -- | `EXTENDING.md` |
| Choose which SHACL/SPARQL engine formulation runs (speed, cross-validation) | `--engine` on `checks`/`data`/`run` | this doc §4, `ARCHITECTURE.md` |
| Customize/add/remove checks after a `pip install`, no local checkout | `--registry`/`--shapes`/`--sparql` | this doc §13 |
| Sequence ontology/taxonomy/data changes as one coordinated rollout | -- | `UPDATING.md` |

## 3. Setup

```bash
pip install ontology-quality-suite          # from PyPI
# or, working in this repo:
uv sync                                     # base
uv sync --extra reasoner                    # + owlready2, real OWL2 DL reasoning (needs Java)
```

Every subcommand takes `-v`/`--verbose` to show exactly what it resolved
before running -- which files matched a glob, which `owl:imports` resolved
and from where, which check engine ran. Reach for it whenever a result
looks surprising, before assuming a bug:

```bash
ontology-quality-suite checks --ontology examples/ontology/domain.ttl --verbose
```

## 4. Use case: auditing an ontology you're actively authoring

The tightest loop: you're editing Turtle by hand and want fast feedback.

```bash
ontology-quality-suite ontology --ontology examples/ontology/domain.ttl --out-dir out/review
```

Open `out/review/report.html`. This alone gets you OntoQA/OQuaRE metrics,
lint flags, documentation-coverage checks, and (always-on) owlrl-based
consistency checking -- no data, no CSVs, nothing else needed.

Want the full 50-check registry too (structural/style/data-quality checks
`ontology` alone doesn't run)?

```bash
ontology-quality-suite checks --ontology examples/ontology/domain.ttl --out-dir out/checks
```

`checks`/`data`/`run` all take `--engine`, choosing which formulation of
the registry actually runs: pyshacl, the portable SPARQL layer, the
optional native (Rust) SHACL engine, or a cross-validating pair of two of
those (default: `native+sparql` if the optional `shacl` package is
installed, else `both` -- `pipeline.default_engine()`). This matters for
speed (pyshacl alone can be ~100x slower than the alternatives on a large
ontology) and, subtly, for which severities get reported at all (pyshacl
has a real, confirmed bug around `sh:severity` inside SPARQL-based
constraints). Don't guess at this from the primer -- `docs/ARCHITECTURE.md`'s
"Choosing which engine actually runs" section has the full comparison,
benchmarks, and the severity caveat in detail.

Want to know if the ontology fits a specific OWL2 profile (e.g. before
feeding it to ELK)? Off by default -- ask explicitly:

```bash
ontology-quality-suite ontology --ontology examples/ontology/domain.ttl --profile EL --out-dir out/review
```

Want a real OWL2 DL reasoner's opinion, not just the always-on
RDFS/OWL2-RL closure (the always-on pass that derives obvious extra facts
and checks for contradictions, e.g. subclass membership -- see "reasoner"
above; a full reasoner goes further, at the cost of needing Java)?

```bash
uv sync --extra reasoner   # owlready2 + a Java runtime, once
ontology-quality-suite ontology --ontology examples/ontology/domain.ttl --reasoner pellet --out-dir out/review
```

**Not `--reasoner hermit` against this particular fixture** --
`examples/ontology/domain.ttl` declares a property with `rdfs:range
xsd:date`, and `xsd:date` isn't part of OWL2's official datatype map, so
HermiT throws `UnsupportedDatatypeException` and the suite degrades
silently to `REA-022` ("reasoner unavailable") rather than actually
running -- correct, graceful error handling on the suite's part, not a
bug, but it means the command silently demonstrates the fallback path
instead of a real reasoner run. Pellet has no such restriction and
correctly finds the same contradiction (`REA-020`/`REA-021`) against this
exact fixture, unmodified -- verified above. See `docs/REASONING.md` for
exactly what each backend catches and its limits (sound-but-incomplete
owlrl vs. complete-but-optional HermiT/Pellet), and this exact `xsd:date`
gotcha specifically.

**Python API**, if you're scripting this rather than using the CLI:

```python
from ontology_suite.ontologyeval import ontology_evaluation as oe
from ontology_suite.reasoning import profile, consistency

graph, import_report = oe.resolve_imports("examples/ontology/domain.ttl")
schema = oe.collect_schema(graph)
metrics = oe.compute_metrics(schema)          # OntoQA/OQuaRE numbers, as a dict

profile_report = profile.check_profiles(graph, profiles=("EL",))
```

## 5. Use case: building and validating a CSV-to-RDF pipeline

The sequence that catches problems *before* they reach real data, cheapest
check first:

```bash
# 1. Does the query even reference vocabulary the ontology declares?
#    No CSV needed -- this is static analysis of the query text.
ontology-quality-suite sketch --queries examples/queries --ontology examples/ontology/domain.ttl --out-dir out/sketch

# 2. Actually run the CSVs through oxi-gen (needs a built oxi-gen binary --
#    see config.find_oxi_gen_binary; pass --oxi-gen-bin if it isn't auto-found).
ontology-quality-suite triplify --csv-dir examples/csv --queries examples/queries --out-dir out/data

# 3. Assess what it actually produced -- structure, conformance to the
#    ontology, and (optionally sampled) reasoning-backed consistency.
ontology-quality-suite data out/data/*.ttl --ontology examples/ontology/domain.ttl --out-dir out/data-eval
```

`sketch`'s `CNF-001`/`CNF-002` findings (a class/property the query builds
that the ontology never declares) are the cheapest possible signal -- they
cost nothing but reading the query text, and catch exactly the class of bug
("query still points at the old namespace after a rename") that otherwise
only shows up once real data looks wrong. Run `sketch` again any time the
ontology changes, before re-running real CSVs through an unchanged query.

For a data graph too large to reason over in full, sample the (expensive)
reasoning pass while still checking the (cheap) SPARQL/SHACL registry over
the whole thing:

```bash
# examples/data/legacy-export.ttl is small -- --sample matters on something
# large enough that the reasoning pass over all of it would actually be slow.
ontology-quality-suite data examples/data/legacy-export.ttl --ontology examples/ontology/domain.ttl \
    --sample 5000 --out-dir out/data-eval
```

Want to check real triplified *output*, not just the query template, from
Python directly (no CLI flag for this one yet)?

```python
from ontology_suite import config
from ontology_suite.triplify import oxigen
from ontology_suite.triplify.discovery import TriplifyJob
from ontology_suite.dataquality import data_quality
import rdflib

binary = config.find_oxi_gen_binary()
oxigen.run_oxi_gen(
    binary, TriplifyJob(csv_path="examples/csv/animals.csv", query_path="examples/queries/animals.rq"),
    "output.ttl",
)

output_graph = rdflib.Graph().parse("output.ttl", format="turtle")
declarations = data_quality.ontology_declarations(rdflib.Graph().parse("examples/ontology/domain.ttl"))
conformance = data_quality.check_conformance(declarations, output_graph)
print(conformance["undeclared_classes_used"], conformance["undeclared_properties_used"])
```

See `docs/TARQL_ALIGNMENT.md` for what each finding kind means and how to
read it after a version bump specifically.

## 6. Use case: releasing a new ontology version

`version-diff` gives an evidence-based MAJOR/MINOR/PATCH classification --
treat it as a release gate, not just a report:

```bash
ontology-quality-suite version-diff examples/gist_versions_reference/gistCore14.0.0.ttl \
    examples/gist_versions_reference/gistCore14.1.0.ttl --out-dir out/version-diff --json
```

| Bump | Action |
|---|---|
| PATCH | Labels/comments only -- merge freely. |
| MINOR | Additive only -- safe to ship ahead of consumers catching up. |
| MAJOR | Something existing data/queries may depend on changed or vanished. Run the pre-deployment compatibility gate below before shipping. |

Before promoting a MAJOR change past review, run representative real
production data through the *proposed* new version -- this is the single
highest-leverage command in the whole suite for avoiding a production
incident, and it costs one command:

```bash
ontology-quality-suite data examples/data/legacy-export.ttl --ontology examples/ontology/domain.ttl \
    --sample 5000 --out-dir out/pre-deploy-check --fail-on Violation
```

(substitute your own production export and proposed new ontology version --
shown here paired with this repo's own fixtures so the command is
runnable as-is)

A non-zero exit here is exactly the signal that this release needs a
migration plan before shipping, not after. **Bump `owl:versionIRI` to
match** -- `QUA-007`/`checks` will flag its absence, and a released MAJOR
change under an unchanged `versionIRI` actively misleads anything that
cached the old one.

Full sequencing rules (additive vs. removal ordering, rename-as-add-plus-
deprecate, rollback strategy, who should approve what) are in
`docs/UPDATING.md` §3-5 -- read that before designing a release process
around this suite, not just this section.

## 7. Use case: an ontology just changed -- will my existing queries survive?

The `sketch` step from §5, run specifically against a *new* ontology
version with *unchanged* queries:

```bash
ontology-quality-suite sketch --queries examples/queries --ontology examples/ontology/domain.ttl \
    --out-dir out/query-compat-check
```

(`--ontology` here stands in for your proposed new version -- substitute
it; the command shape is otherwise identical to §5's step 1)

Any `CNF-001`/`CNF-002` here names exactly which query references
vocabulary the new version no longer declares -- caught before a single row
of real data is triplified. Pair with `--verbose` to see exactly which
query files were checked, especially useful when `--queries` points at a
folder with a non-default `--file-pattern`.

## 8. Use case: an ontology renamed something -- can the fix be automated?

`consistency` combines version-diffing, rename detection, and TARQL
alignment into one report *with suggested repair diffs*, not just findings.
No committed fixture in `examples/` demonstrates a deliberate rename with a
migration annotation, so the filenames below are illustrative --
substitute your own two ontology versions and query folder:

```bash
ontology-quality-suite consistency --new domain-v2.ttl --old domain-v1.ttl --queries queries/ \
    --out-dir out/consistency
```

Rename detection works two ways, in priority order: an explicit
`owl:equivalentClass`/`dcterms:isReplacedBy` migration annotation in the new
version (confidence 1.0, catches any rename regardless of spelling), or
local-name similarity as a fallback (typos, capitalization, a
namespace-only bump). **Leave a migration annotation when retiring an
IRI** -- it costs one triple and is what makes automatic repair possible at
all for a genuine semantic rename.

By default this writes `.patch` files under `--out-dir/repairs` for review,
dry-run:

```bash
ontology-quality-suite consistency --new domain-v2.ttl --queries queries/ \
    --apply-repairs --min-confidence 0.7
```

`--apply-repairs` writes straight to the target files; `--min-confidence`
gates which suggestions are trustworthy enough to apply unattended (a
one-line `PREFIX` fix is confidence 1.0; an ontology-declaration stub
guessing at a property's domain/range is 0.7 -- review those, don't
blanket-apply at a low threshold). See `docs/CONSISTENCY_AND_REPAIR.md` for
the full table of finding-kind -> fix-kind -> confidence.

**Python API**, for scripting a repair pass rather than using the CLI:

```python
from ontology_suite import consistency

report = consistency.check_consistency(
    "domain-v2.ttl", old_ontology="domain-v1.ttl", tarql_sources=["queries/"],
)
print(consistency.format_consistency_report(report))
consistency.write_repair_patches(report.repairs, "out/repairs")     # dry run
consistency.apply_repairs(report.repairs, min_confidence=0.7)       # or apply directly
```

A *registry*-finding repair (not TARQL/version-specific -- e.g. `STR-001`,
`QUA-002`) is a separate, general-purpose SPARQL-1.1-Update-generating
engine, `checks.repair` -- same idea, different input.

## 9. Use case: governing a taxonomy of controlled values alongside the ontology

If your data uses a controlled vocabulary (SKOS concepts, `gist:Category`
individuals) layered on top of the ontology, `consistency` alone has a
blind spot: it has no notion of a taxonomy, so a query hard-coding a
nonexistent taxonomy value reports clean. `pattern-consistency` checks that
specific boundary, plus the other three around it, in one pass:

```bash
ontology-quality-suite pattern-consistency \
  --queries examples/pattern_consistency/transform.rq \
  --ontology examples/pattern_consistency/ontology.ttl \
  --taxonomy examples/pattern_consistency/taxonomy.ttl \
  --out-dir out/pattern-consistency
```

Run **both** `consistency` and `pattern-consistency` if you have a taxonomy
layer at all -- they check different, non-overlapping boundaries, and
neither substitutes for the other (see
`docs/MODELLING_PATTERN_CONSISTENCY.md`'s own worked example of exactly
this gap). Add `--output-data` to also check real triplified output, and
`--dot out.dot` to visualise where in the query's shape a gap sits.

## 10. Use case: running against a live Fuseki triplestore instead of files

Needs a real running Fuseki (or any SPARQL 1.1 Protocol store) -- the
endpoint URLs, credentials, and graph URIs below are illustrative,
substitute your own. Once data lives in a triplestore rather than local
files, three checks apply per named graph, each independent
(`docs/FUSEKI.md` has the full design rationale for why a
dataset-of-named-graphs needs its own approach, not just "point the
existing checks at a URL"):

```bash
ontology-quality-suite consistency-remote \
  --query-endpoint http://localhost:3030/mydataset/sparql \
  --manifest graphs.json \
  --auth-user admin --auth-password secret \
  --out-dir out/consistency-remote --fail-on-misalignment
```

`graphs.json` is how you tell the store which named graph is the ontology
and which local query file produced which data graph -- a triplestore has
no built-in way to record that provenance itself:

```json
{
  "graphs": [
    { "graph_uri": "https://example.org/graph/ontology/2.0.0", "role": "ontology" },
    {
      "graph_uri": "https://example.org/graph/triplified/animals",
      "role": "triplified_data",
      "source_tarql": "queries/animals.rq",
      "ontology_graph_uri": "https://example.org/graph/ontology/2.0.0"
    }
  ]
}
```

For anything beyond the three-way consistency check -- diffing two ontology
*versions* held as separate named graphs, or running the registry's own
`.rq` checks scoped to exactly the graphs they should see -- use the
`remote.fuseki` Python API directly:

```python
from ontology_suite.remote import fuseki
from ontology_suite.versioning import diff as version_diff

dataset = fuseki.FusekiDataset(query_endpoint="http://localhost:3030/mydataset/sparql")
old_graph = fuseki.load_named_graph(dataset, "https://example.org/graph/ontology/1.0.0")
new_graph = fuseki.load_named_graph(dataset, "https://example.org/graph/ontology/2.0.0")
diff, bump = version_diff.diff_ontologies(old_graph, new_graph)
```

A computed repair (`checks.repair.compute_repair` locally, or
`consistency.check_consistency`'s suggestions) is a real SPARQL 1.1 Update
either way -- apply it to a live dataset the same way:

```python
fuseki.apply_repair_remote(dataset, outcome.update_text)
```

Full field-by-field manifest spec, the graceful-degradation table for
partial bindings, and `--sample-limit` are in `docs/FUSEKI.md`.

## 11. Use case: producing something a non-technical stakeholder can read

Every check above produces `report.html`/`full_results.csv` (start with the
former), but for the ontology's own structure -- not findings -- `docgen`
generates a standalone reference page:

```bash
ontology-quality-suite docgen --ontology examples/ontology/domain.ttl --out-dir out/docgen
```

Class/property tables, whole-ontology Mermaid diagrams (hierarchy,
import-alignment, object-property graph), and -- per class -- a real
Graphviz `.svg`/`.png` diagram of everything directly said about that one
class (its "concise bounded description," in RDF terms), collapsed by
default so a hundred-class ontology doesn't open
with a hundred images rendered at once. `--ref other-ontology.ttl`
(repeatable) resolves cross-references to imported vocabulary; add
`--instances data.ttl` to also show per-class individual counts from real
data. See `docs/CLASS_DIAGRAMS.md` for exactly what each diagram shows and
why (the blank-node/list/literal rendering conventions apply to every
diagram this suite produces, including `pattern-consistency --dot`'s gap
visualisations).

`docgen` is not part of `run`'s default output (it's reference
documentation, not a quality assessment -- no `ResultRow` findings at all).
Add `--docgen` to `run` to also generate it there, or run `docgen` on its
own.

## 12. Use case: wiring this into CI, across the whole ontology/taxonomy/data lifecycle

Condensed from `docs/UPDATING.md` (read that in full before designing a
process around this -- it also covers sequencing, rollback, and who should
approve what):

```bash
# Ontology PR
ontology-quality-suite ontology --ontology examples/ontology/domain.ttl --fail-on Violation
ontology-quality-suite checks --ontology examples/ontology/domain.ttl --fail-on Violation
ontology-quality-suite version-diff examples/gist_versions_reference/gistCore14.0.0.ttl \
    examples/gist_versions_reference/gistCore14.1.0.ttl --fail-on major

# Taxonomy PR -- treat as a data update against the ontology
# (taxonomy/, taxonomy-queries/ are illustrative -- substitute your own; the
# command shape is otherwise identical to §5's triplify -> data sequence)
ontology-quality-suite triplify --csv-dir taxonomy/ --queries taxonomy-queries/ --out-dir out/taxonomy
ontology-quality-suite data out/taxonomy/*.ttl --ontology examples/ontology/domain.ttl --fail-on Violation

# Data-pipeline PR (a changed oxi-gen query) -- before touching real data
ontology-quality-suite sketch --queries examples/queries --ontology examples/ontology/domain.ttl --fail-on Violation
```

A minimal GitHub Actions gate combining the first two:

```yaml
name: Ontology checks
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run ontology-quality-suite ontology --ontology domain.ttl --fail-on Violation
      - run: uv run ontology-quality-suite checks --ontology domain.ttl --fail-on Violation
```

**Continuous drift detection**, independent of any explicit change (an
upstream source system can silently start sending a value the ontology
doesn't expect even when nothing here changed): schedule `data` against
current production ontology + current production data on a recurring
cadence, e.g. a nightly cron job or scheduled workflow.

## 13. Use case: extending the suite with a project-specific check

### Where the checks actually live, and how to point at your own

`registry.json`, `shapes/*.ttl`, and `sparql/**/*.rq` (referenced with bare
paths below and throughout `docs/EXTENDING.md`, and in this repo at the
paths those bare names suggest -- `registry.json`, `shapes/`, `sparql/` at
the repo root) are, once `pip install`ed, bundled *inside* the package, not
at those repo-root paths at all:

```python
from ontology_suite import config
print(config.PACKAGE_RESOURCES)   # <site-packages>/ontology_suite/resources/
print(config.DEFAULT_SHAPES_DIR)  # .../resources/shapes
print(config.DEFAULT_SPARQL_DIR)  # .../resources/sparql
```

You do **not** need a local checkout of this repo, or to edit files inside
`site-packages`, to customize checks -- `checks`/`data`/`run` each take
`--registry`/`--shapes`/`--sparql`, independently overridable, defaulting
to exactly the installed package paths above. Point them at your own copy
instead:

```bash
# once: find and copy the installed defaults somewhere you can edit
python -c "from ontology_suite import config; print(config.PACKAGE_RESOURCES)"
cp -r <the path printed above> my-checks

# remove a check: delete its .rq file -- checks/data/run discover checks by
# walking the directory (sparql_dir.rglob('*.rq')), not from any manifest
rm my-checks/sparql/quality/QUA-004.rq

# add one: a new registry.json entry + a new .rq file (see below)

# run against your copy instead of the installed defaults
ontology-quality-suite checks --ontology examples/ontology/domain.ttl \
  --registry my-checks/registry.json --shapes my-checks/shapes --sparql my-checks/sparql
```

Verified against a real, isolated `pip install`: deleting `QUA-004.rq` from
the copy and pointing `--sparql`/`--shapes`/`--registry` at it drops its 9
findings against `examples/ontology/domain.ttl` to 0, while the installed
package's own copy is untouched (`--engine sparql`; the SHACL side of a
removal works the same way, except shapes are one file per *category*, so
removing a single check means editing that category's `.ttl` file rather
than deleting a whole file).

### Adding a new check

Three ways, cheapest first -- pick the lightest one that
fits (`docs/EXTENDING.md` has the full walkthrough, including the
`registry.json` entry format and a worked "wire it in" example):

1. **A portable SPARQL `CONSTRUCT` check** -- anything expressible as a
   graph pattern over one merged graph. Add a `registry.json` entry and a
   `sparql/<category>/<id>.rq` file; nothing else to wire up.
2. **Rerunning an existing check against a transformed graph** (e.g. an
   owlrl closure) -- same as (1), just placed under `sparql/reasoning/`.
3. **A native Python check** -- when there's no single graph to
   pattern-match (OWL2 profile membership, a real DL reasoner's output,
   comparing against a *separate* ontology's declarations). Still needs a
   `registry.json` entry; write a function returning `ResultRow` instances
   directly and wire it into the relevant `pipeline.py` stage.

Regenerate `docs/CHECKS.md` after any registry change:
`python docs/generate_checks_md.py`. **Actually run a new check against a
fixture that should and shouldn't trigger it** before trusting it -- every
real bug this suite's own checks have shipped with (a `FILTER` that
silently matched nothing, a namespace filter disabled by a
`urllib.parse.urljoin` quirk) looked correct on read-through and only
surfaced once actually executed.

## 14. A maturity path, if adopting this suite from scratch

Roughly the order real usage tends to accumulate value, each step
composing with what came before rather than replacing it:

1. **`ontology` + `checks`** against your existing ontology, locally,
   ad hoc -- establish a baseline, fix what's `Violation`-severity.
2. **Wire §12's CI gate** so the baseline doesn't regress.
3. **`sketch` before `triplify`** once you have a real CSV-to-RDF pipeline
   -- catch query/ontology drift before it reaches data.
4. **`version-diff` + the pre-deployment gate** (§6) once you're shipping a
   second ontology version -- this is where most real incidents this suite
   is designed to prevent actually happen.
5. **`consistency` with `--apply-repairs`** once version bumps are frequent
   enough that hand-fixing every TARQL query after each one is real,
   recurring toil.
6. **`pattern-consistency`** if/when a taxonomy layer enters the picture --
   don't add it speculatively; it's a real, separate check with its own
   file set to maintain.
7. **`consistency-remote` + `remote.manifest`** once data lives in a live
   triplestore rather than files checked out locally.
8. **`docgen`**, whenever a non-technical stakeholder first asks "can I
   just see what's in the ontology" -- cheap to add at any point, no
   dependency on the steps above.

Nothing here requires steps in strict order -- a project with no
triplification pipeline at all (ontology-only work) legitimately stops
after step 2, permanently, and that's a complete, valid use of this suite,
not a partial one.

## 15. Want to see it all together?

Every use case above uses its own small, isolated fixture. For one
continuous, realistic worked example running through most of the suite in
the order a real project would actually hit it, see
`docs/ACME_ROBOTICS_WALKTHROUGH.md` -- a small org chart built on two real
external vocabularies (W3C Organization Ontology, FOAF), with two runnable
companion notebooks.
