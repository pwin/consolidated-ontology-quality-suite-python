# Consistency checking and automated repair

This is the capability this package (`consolidated-ontology-suite-python`)
adds on top of the inherited `ontology_suite` pipeline (see
`ARCHITECTURE.md` for that): given one or more ontology versions and one or
more TARQL/oxi-gen transformation files, run every consistency check this
suite has and get back a **suggested diff**, not just a finding.

Three modules, one entry point:

- `ontology_suite.versioning.diff` / `ontology_suite.versioning.rename_detection`
  -- has the ontology changed between two versions, and how? Ported from the
  original `ontology_suite`; `rename_detection` is new here.
- `ontology_suite.sketch.prefix_alignment` -- do the given TARQL/oxi-gen
  queries still use the right namespaces, and only reference
  classes/properties the ontology actually declares? Ported unchanged.
- `ontology_suite.repair.tarql_repair` / `ontology_suite.checks.repair` --
  new: turns the findings above into concrete, appliable diffs.
- `ontology_suite.consistency` -- new: the entry point tying all of the
  above together.

## Quick start

```python
from ontology_suite import consistency

report = consistency.check_consistency(
    "domain-v2.ttl",
    old_ontology="domain-v1.ttl",         # optional -- omit to skip version-diff
    tarql_sources=["queries/"],            # optional -- omit to skip TARQL alignment
)
print(consistency.format_consistency_report(report))

# Dry run: write each suggestion's unified diff as a .patch file for review
consistency.write_repair_patches(report.repairs, "out/repairs")

# Or apply directly (writes to the actual target files)
consistency.apply_repairs(report.repairs, min_confidence=0.7)
```

Or from the command line:

```
ontology-suite consistency --new domain-v2.ttl --old domain-v1.ttl --queries queries/
ontology-suite consistency --new domain-v2.ttl --queries queries/ --apply-repairs --min-confidence 0.7
```

`--apply-repairs` writes straight to the target files; without it, suggested
diffs are written as `.patch` files under `--out-dir/repairs` for review
first -- the same "dry-run by default, opt in to the real edit" convention
`version-diff --fail-on` and the rest of this suite's CLI already follow.

## How a rename is detected

The hard part isn't finding that a TARQL query references something the
ontology no longer declares -- `sketch.prefix_alignment.check_undeclared_terms`
already does that. The hard part is telling "this is the old name for a
class the ontology renamed" apart from "this is genuinely obsolete
vocabulary, fix the query" -- because both look identical from the query's
side (an IRI the ontology doesn't declare).

`versioning.rename_detection.detect_renames` uses two signals, in priority
order:

1. **Explicit migration annotation.** If the *new* ontology version asserts
   `oldTerm owl:equivalentClass newTerm` (or `owl:equivalentProperty`, or
   `dcterms:isReplacedBy`) -- confidence 1.0, regardless of how different
   the local names are. This is the only signal that catches a genuine
   *semantic* rename (`ex:Widget` -> `ex:Product`). **If you want this
   suite's repair suggestions to resolve a rename automatically, leave this
   annotation behind when retiring the old IRI** -- it costs one triple and
   is good practice for human readers too.
2. **Local-name similarity** (identical, or a close match) -- the fallback
   when no explicit annotation exists. This only catches renames that kept
   a similar spelling: typo fixes, capitalization changes, or a
   namespace-only bump (same local name, moved to a new namespace IRI --
   the gist-style versioned-namespace pattern). It cannot infer a rename
   between two unrelated words from structure alone.

Without either signal, an undeclared term is treated as genuinely new
vocabulary, and the suggested fix is an ontology-declaration stub instead of
a rename.

## The three kinds of suggested fix

| Finding | Fix | Kind | Confidence |
|---|---|---|---|
| `namespace_mismatch` (query rebinds a prefix the ontology already uses, to a different IRI) | Rewrite the query's one `PREFIX` line | `update_prefix` | 1.0 if the ontology set has exactly one candidate IRI for that prefix, else 0.5 (ambiguous -- review before applying) |
| `prefix_name_mismatch` (same namespace, different prefix label) | Rename the query's prefix label to match the ontology's | `rename_prefix` | 0.6 (cosmetic, not a bug) -- 0.4 if ambiguous; skipped entirely if the target label would collide with an unrelated existing prefix in the same file |
| `undeclared_namespace` (namespace not declared anywhere in the ontology set) | *(none)* | -- | -- deliberately no suggestion; guessing what an unrecognized namespace was supposed to be isn't safe to automate |
| `undeclared_class`/`undeclared_property` matched by a detected rename | Substitute every occurrence of the old term for the new one in the query | `rename_iri` | inherits the rename's confidence (1.0 for an explicit annotation, lower for a fuzzy name match) |
| `undeclared_class`/`undeclared_property` with no matching rename | Append an `owl:Class`/`rdf:Property` declaration stub to the ontology | `insert_ontology_stub` | 0.7 -- properties are declared generically as `rdf:Property` (not guessing object- vs. datatype-property); review before committing |

One thing to expect, not a bug (inherited from `TARQL_ALIGNMENT.md`): a pure
namespace bump produces *two* findings for the same root cause -- a
`namespace_mismatch` and an `undeclared_class`/`undeclared_property` for the
same term. `repair.tarql_repair` already accounts for this: the
`namespace_mismatch` fix is generated, and the ontology-stub suggestion for
the same namespace is suppressed (since applying the one-line `PREFIX` fix
resolves the undeclared-term finding on its own). A real rename (different
local name) generates its own `rename_iri` fix and is likewise excluded
from the ontology-stub pass, so each root cause gets exactly one suggestion.

## `checks.repair`: the general registry-check quick-fix engine

Separately from the TARQL/version-specific repairs above,
`ontology_suite.checks.repair` ports the webapp's `repairEngine.ts`: a
Schematron-Quick-Fix-style engine that turns a *registry* finding
(`STR-001`, `QUA-002`, `MDL-003`, ...) into a real SPARQL 1.1 Update,
applied against an in-memory `rdflib.Graph` (no `oxigraph` dependency needed
here -- rdflib has its own SPARQL 1.1 Update support). The templates
themselves (`resources/repairs/*.ru` + `manifest.json`) are exactly the
webapp's own files, so the two stay in lockstep by construction. See
`checks/repair.py`'s module docstring for the variable-binding contract
each template can use.

The same computed `RepairOutcome.update_text` is what
`remote.fuseki.apply_repair_remote` posts to a live Fuseki `/update`
endpoint -- one template, two execution backends (local in-memory graph, or
a real triplestore). See `FUSEKI.md`.
