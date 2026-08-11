# Updating the semantic specification ecosystem: a playbook

This is operational guidance, not a spec: recommendations for how to change
an **ontology** (OWL2 TBox: classes, properties, axioms), a **taxonomy**
(the controlled vocabulary of values data actually uses -- SKOS concepts,
`gist:Category` individuals, code lists -- built *on* the ontology's
classes/properties but distinct from them), and the **data graph** (real
ABox triples, however triplified) as one coordinated system rather than
three independently-versioned artifacts that happen to reference each
other. Every command below is this suite's own CLI; none of this requires
tooling beyond what's already in the repo.

## 1. Why these three layers can't be updated independently

The dependency direction runs one way for *additions* and the opposite way
for *removals*:

- **Ontology -> taxonomy.** A taxonomy entry is (typically) an individual
  of an ontology class -- e.g. a `gist:Category` instance, or a
  `skos:Concept` whose `skos:ConceptScheme` is scoped by a class the
  ontology defines. You cannot add a taxonomy entry of a kind the ontology
  hasn't declared yet.
- **Ontology + taxonomy -> data.** Real triples use ontology
  classes/properties (structure) *and* reference taxonomy individuals
  (controlled values) as the objects of properties. Data can't validly use
  either before both exist.
- **Data -> taxonomy -> ontology, in reverse, for removals.** Removing an
  ontology class while data still asserts instances of it, or retiring a
  taxonomy entry while data still points at it, produces exactly the
  findings this suite's `checks`/`data` stages exist to catch:
  `STR-001`/`CNF-001` (undeclared/unreferenceable class),
  `DAT-002`/`CNF-002`-adjacent dangling references. Removing structure
  before consumers stop depending on it is the single most common cause of
  a "the ontology update broke production data" incident.

This asymmetry is the basis for the sequencing rules in §3.

## 2. Entry points for making changes

Each layer has a different natural author, a different authoring surface,
and a different blast radius if done carelessly -- entry points and review
rigor should track that, not be uniform across all three.

### 2.1 Ontology structural changes

**Author:** ontologist / data architect. **Surface:** the ontology's own
Turtle source (or an upstream import bump, e.g. a new gist release --
`examples/gist_versions_reference/` in this repo exists specifically to
validate tooling against that exact scenario). **Blast radius:** highest --
a structural change can silently invalidate every downstream data graph and
every `oxi-gen` query that references the changed term.

Every ontology PR should run, before merge:

```
ontology-quality-suite ontology --ontology domain.ttl --out-dir out/ontology-review
ontology-quality-suite version-diff previous-release.ttl domain.ttl --out-dir out/version-diff
```

`version-diff`'s bump classification (MAJOR/MINOR/PATCH) is the single most
useful triage signal a reviewer gets in one command -- see §3.4 for how it
should gate what happens next, and `docs/VERSIONING.md` for exactly what it
checks and its documented limits (blank-node handling, hierarchy-aware
domain/range comparison, validated against real gist release history).

### 2.2 Taxonomy / controlled-vocabulary changes

**Author:** domain SME / data steward, usually *not* the ontologist.
**Surface:** frequently a lighter-weight, business-friendly format (a
spreadsheet or CSV of controlled terms) rather than hand-authored Turtle --
which makes a taxonomy update structurally identical to a `triplify` job:
a CSV of new/changed terms run through a small `CONSTRUCT` query that
mints `gist:Category`/`skos:Concept` individuals. **Blast radius:** medium
-- additions are cheap and safe (see §3.1); retirements need the same care
as an ontology removal, just one layer down.

Because a taxonomy update is "just" a small triplification, it should go
through the *same* pipeline as any other data update (§2.3), not a bespoke
process: `ontology-quality-suite triplify` against the taxonomy's own CSV+query,
then `ontology-quality-suite data` against the ontology to confirm every new
individual's type and any `skos:broader`/`gist:Category`-hierarchy
assertions actually conform.

### 2.3 Data graph / triplification pipeline changes

**Author:** data engineer, usually reacting to an upstream source-system
change (new CSV columns, a new source feed) rather than initiating the
change themselves. **Surface:** the `oxi-gen`/tarql `CONSTRUCT` query
mapping source columns to RDF. **Blast radius:** contained to whatever
that query touches, *provided* the ontology/taxonomy it targets hasn't
also moved out from under it -- which is exactly what `sketch` (§4.3)
checks before you run real data through it.

### 2.4 Intake and review gate

Regardless of layer, treat every change as a reviewable unit with:

1. A stated *reason* (new source system, new business term, a bug in a
   prior axiom) -- this determines expected severity (§3.4), not the other
   way around.
2. The relevant `ontology-quality-suite` command(s) run and attached to the
   review, not re-derived by the reviewer from memory.
3. For anything touching the ontology or taxonomy: the regenerated
   `ontology-quality-suite docgen` output, diffed against the previous version, as
   the human-readable "what actually changed" artifact -- structural diffs
   in Turtle are hard to read; a rendered class/property table is not.

## 3. Sequencing taxonomy, ontology, and triples updates

### 3.1 Additive changes: ontology, then taxonomy, then data

New class, new property, new controlled term, new data using it -- roll
out in dependency order:

1. Merge and deploy the ontology change (new class/property/axiom).
2. Add the taxonomy entries that depend on it (new `gist:Category`
   instances, etc.), validated with `ontology-quality-suite data` against the
   *deployed* ontology.
3. Only then point `oxi-gen` queries and real CSV ingestion at the new
   terms -- validate the query shape first with `ontology-quality-suite sketch`
   (§4.3), then `triplify`, then `ontology-quality-suite data` on the result.

Nothing here is time-critical -- an ontology addition that nothing uses
yet is inert. This is why purely additive ontology changes classify as
MINOR (`docs/VERSIONING.md`): safe to ship ahead of its consumers.

### 3.2 Deprecating or removing: reverse order, data first

1. **Stop producing new triples** that use the term being retired (update
   the `oxi-gen` query first).
2. **Migrate or accept existing data** that already uses it -- either
   re-triplify historical batches against the new mapping, or explicitly
   accept that old batches will keep failing `checks`/`data` against the
   *new* ontology version (see the environment-promotion gate in §3.5 --
   this is a real, visible trade-off, not something to discover by
   accident in production).
3. **Retire the taxonomy entry** (mark deprecated -- `owl:deprecated true`
   or a taxonomy-specific status property -- rather than delete outright,
   so historical data referencing it doesn't become a dangling reference).
4. **Only then** remove or deprecate the ontology term itself. Prefer
   `owl:deprecated true` over deletion in almost every case: deletion
   makes `STR-001`/`CNF-001` fire against every historical data batch that
   used the term, forever; deprecation lets `QUA-003` (deprecated term
   still in active use) surface *new* usage without breaking old data.

This is the mirror image of §3.1, and for the same reason: removing
structure a consumer still depends on is what actually breaks things, not
adding structure nothing depends on yet.

### 3.3 Renames are "add + deprecate", never an in-place edit

RDF has no native rename. Treat a rename as an addition (§3.1) of the new
term plus a deprecation (§3.2) of the old one, run in parallel with an
explicit migration window between them -- never edit a term's IRI in
place and call it done, since every existing triple, taxonomy entry, and
`oxi-gen` query still points at the old IRI until each is migrated.

### 3.4 Let `version-diff`'s bump level gate what happens next

`ontology-quality-suite version-diff old.ttl new.ttl` (`docs/VERSIONING.md`)
gives an evidence-based MAJOR/MINOR/PATCH classification -- use it as a
gate, not just a report:

| Detected bump | What it means for sequencing |
|---|---|
| **PATCH** | Labels/comments only. No data-compatibility risk; merge and deploy freely. |
| **MINOR** | Additive only (§3.1). Safe to deploy ahead of taxonomy/data catching up. |
| **MAJOR** | Something existing data or queries may depend on changed or disappeared. Do **not** deploy until the §3.5 pre-deployment data-compatibility gate has been run against representative real data, and a migration/communication plan exists for consumers. |

Treat a mismatch between the tool's classification and the version bump
the author actually intends to publish (e.g. tool says MAJOR, author wants
to ship it as a patch release) as something requiring explicit
justification in the review, not something to silently override --
`docs/VERSIONING.md` documents exactly which structural facts drove the
classification, so that justification is concrete, not a judgment call.

### 3.5 Cross-environment promotion, with a pre-deployment compatibility gate

Before promoting an ontology/taxonomy change past a review environment,
run the *existing* (or a representative sample of) production data through
it against the **proposed new** ontology version -- catching a conformance
break before it ships costs one command and finds the problem in minutes
instead of in a production incident:

```
ontology-quality-suite data existing-data-export.ttl --ontology proposed-new-version.ttl \
    --sample 5000 --out-dir out/pre-deploy-check --fail-on Violation
```

A non-zero exit here (new `CNF-00x` conformance violations, new reasoning
contradictions) is exactly the signal that a MAJOR-classified change
needs a migration plan before it ships, not after.

## 4. Tooling for update deployment

### 4.1 Pre-merge CI gate, per layer

- **Ontology PR:** `ontology-quality-suite ontology` (fails the build on any
  `Violation`; OWL2 profile checks are opt-in per `--profile`, see the
  ontology's actual expressiveness goals) + `ontology-quality-suite checks` +
  `version-diff` against the previous released version.
- **Taxonomy PR:** treat as a data update (§2.2) -- `triplify` the taxonomy
  CSV, then `ontology-quality-suite data` against the ontology.
- **Data-pipeline PR (a changed `oxi-gen` query):** `ontology-quality-suite sketch`
  (§4.3) before touching real data.

### 4.2 Pre-deployment data-compatibility gate

Covered in §3.5 -- this is the step most existing ontology-update
workflows skip, and the one this suite makes cheapest to actually run.

### 4.3 `oxi-gen`/tarql query compatibility check

Before running real CSVs through a changed `oxi-gen` query (or before an
ontology change ships, to check *existing* queries against it), sketch the
query's graph shape and diff it against the ontology directly, without
needing any CSV data at all:

```
ontology-quality-suite sketch --queries pipeline/queries/ --ontology proposed-new-version.ttl \
    --out-dir out/query-compat-check
```

`CNF-001`/`CNF-002` findings here mean a query references a class/property
the new ontology no longer declares -- caught before a single row of real
data is triplified.

### 4.4 Versioning discipline

`QUA-005`/`006`/`007`/`008` (this suite's ontology-identity checks) enforce
the mechanics an update process depends on being able to trust:

- The ontology has an actual identifying IRI (`QUA-005`), distinct from the
  namespace IRI minting its terms (`QUA-006`) -- without this, "which
  version is deployed" isn't even askable.
- An `owl:versionIRI` exists and changes every release (`QUA-007`) --
  `version-diff` needs two *different* files to compare in the first
  place, and consumers need a stable way to pin a version.
- Both use `https://` (`QUA-008`) -- treat this as non-negotiable for
  anything meant to be dereferenced externally.

Bump the `owl:versionIRI` to match (or exceed) `version-diff`'s suggested
level every release -- a released MAJOR change under an unchanged
`versionIRI` is worse than no versioning at all, since it actively lies to
anything that cached the old one.

### 4.5 Rollback strategy

Never truly delete a previously-released `owl:versionIRI`'s content --
keep it resolvable (a tagged file, a retained named graph) even after a
newer version ships. Rollback then becomes "repoint consumers/config at
the old `versionIRI`," not "reconstruct what used to be there." This is
the same reasoning as "prefer `owl:deprecated true` over deletion" in
§3.2, one level up: an old *version* is exactly as much a dependency as an
old *term*.

### 4.6 Change communication

Two artifacts, two audiences, both already produced by this suite:

- **`ontology-quality-suite docgen`** regenerated and diffed (or just linked,
  before/after) -- the human-readable "what does the ontology actually
  look like now" artifact for domain reviewers and downstream consumers
  who don't read Turtle. Open both versions in
  [turtle-editor-viewer](https://semantechs.co.uk/turtle-editor-viewer-new/)
  side by side for anything needing interactive exploration.
- **`version-diff --json`** -- the machine-readable "what changed
  structurally, and how severely" artifact, suitable for a release-notes
  generator or a changelog bot, not just a human reading `diff.txt`.

### 4.7 Continuous drift detection

Data can drift out of conformance even when neither the ontology nor the
taxonomy changes -- an upstream source system quietly starts sending a
value the ontology's `rdfs:range` doesn't expect, for instance. Schedule
`ontology-quality-suite data` (with `--sample` if the data graph is large) against
the *current* production ontology and *current* production data on a
recurring cadence, independent of any explicit change -- this catches
exactly the class of problem that only shows up between releases, not at
one.

## 5. Roles and governance

Different layers, different natural owners, different approval bar:

| Layer | Typical owner | Approval bar |
|---|---|---|
| Ontology structure | Ontologist / data architect | Highest -- `version-diff` MAJOR requires a migration plan (§3.4), not just a reviewer's sign-off |
| Taxonomy / controlled values | Domain SME / data steward | Medium for additions, same as an ontology change for retirements (§3.2) |
| Data / triplification pipeline | Data engineer | Scoped to what the query touches, gated by §4.3 before real data moves |

Requiring a *different* approver for ontology-structure PRs than for
taxonomy-value PRs is deliberate, not bureaucratic overhead: a domain SME
is exactly the right reviewer for "should `Widget` be a valid `Category`
value" and typically the wrong reviewer for "should `hasCategory`'s range
be narrowed" -- the latter needs someone who can reason about every
existing consumer of that property, which `version-diff` and the
pre-deployment gate (§3.5) exist to make tractable for a human to actually
do.

## 6. Common pitfalls

Grounded in real, previously-unnoticed bugs this suite's own tooling
surfaced only once it was actually run end to end (see
`docs/EXTENDING.md`'s note on testing new checks, and `docs/VERSIONING.md`)
-- the general lesson generalizes past this specific codebase:

- **Don't trust a heuristic or a diff tool until you've run it against a
  case with a known-correct answer.** `version-diff` was validated against
  six real, independently-published gist releases specifically because a
  synthetic example can't be trusted to exercise every real-world shape a
  change takes (`docs/VERSIONING.md`'s blank-node bug is the concrete
  example: every anonymous class expression looked "removed and
  re-added" until that was caught against real data).
- **Symmetric-looking RDF properties often aren't, post-reasoning.**
  `owl:disjointWith` is semantically symmetric but a reasoner's closure
  may not materialize both directions -- a sequencing/validation check
  that assumes symmetry can silently miss half of what it's meant to
  catch (`docs/REASONING.md`).
- **A namespace/path-handling quirk can silently disable an entire check
  category.** The ported namespace-legend filter matched nothing for a
  released period because of a `urllib.parse.urljoin` edge case -- the
  lesson isn't "check urljoin," it's "a validation step that always
  reports zero findings is not evidence of a clean bill of health; it's
  something to verify independently before trusting it in a release gate."
- **Removing structure is what breaks production, not adding it** -- the
  organizing principle behind §3's entire sequencing rule set, worth
  restating on its own: when in doubt about ordering, additions can go
  first, removals must go last.
