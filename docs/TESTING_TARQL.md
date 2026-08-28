# Testing TARQL/oxi-gen query files

A folder of TARQL `CONSTRUCT` queries is a program. It has no compiler, no
type checker and no test runner of its own, and every one of its failure
modes produces *valid output* -- triples that parse, load and query cleanly,
and are wrong. That is what makes query bugs expensive: nothing fails at the
point of the mistake, and the symptom appears somewhere else entirely, often
months later and usually as "why are there two nodes for this road".

This document is about catching those before they reach data. It covers what
can go wrong, which of this suite's checks catches each thing, what none of
them can catch, and a review order that puts the cheapest checks first.

`TARQL_ALIGNMENT.md` is the companion for one specific case -- prefix and
namespace drift between queries and the ontology -- and goes deeper on it
than this doc does.

## The four things that go wrong

**1. The query references vocabulary the ontology doesn't have.** A typo, a
stale namespace after a rename, a term removed in a new ontology version.
Caught by `CNF-001`/`CNF-002`, cost: reading the query text.

**2. The same conceptual IRI is minted two different ways in two files.**
Both queries are valid. Both produce triples. The two IRIs simply never
join, and the break surfaces later as a dangling reference or a duplicate
entity. Caught by `TQL-001`.

**3. A variable in the `CONSTRUCT` template is never bound.** Every triple
using it is silently dropped -- for every input row, with no error. Caught by
`TQL-002`/`TQL-003`.

**4. The query builds a shape the ontology forbids.** A property used outside
its declared domain, a value outside its range. Caught by `CNF-003`/`CNF-004`.

## The checks, cheapest first

Everything below runs from one command:

```bash
ontology-quality-suite sketch --queries scripts/to_rdf --out-dir out/sketch
```

`--ontology` is optional. Without it you still get the whole `TQL-00x` half,
because that half reads the query source rather than comparing against
anything.

### Query source review (`TQL-001`, `TQL-002`, `TQL-003`)

No ontology, no CSV, no triplification. `sketch/bind_analysis.py` parses each
query's `BIND` statements and `CONSTRUCT` template and writes
`out/sketch/bind-review.txt`.

| Check | Severity | Fires when |
|---|---|---|
| `TQL-001` | Warning | One variable is bound by structurally different expressions in different files |
| `TQL-002` | Violation | A `?something_IRI` variable is used in `CONSTRUCT` but never bound |
| `TQL-003` | Info | Any other `CONSTRUCT` variable is unbound -- probably a CSV column, worth confirming |

**`TQL-001` compares skeletons, not text.** The expression is reduced by
replacing every `?var` with `?` before comparing. This is the difference
between a check people use and a check people mute:

```sparql
# NOT reported -- same template, different column name. Ordinary.
BIND(... CONCAT("exd:_Road_", ?roadid)   ... AS ?road_IRI)     # a.rq
BIND(... CONCAT("exd:_Road_", ?roadname) ... AS ?road_IRI)     # b.rq

# Reported -- the template itself differs, so the IRIs differ whenever
# the value contains a space.
BIND(... CONCAT("exd:_Road_", ?roadid)                    ... AS ?road_IRI)
BIND(... CONCAT("exd:_Road_", REPLACE(?roadid," ","_"))   ... AS ?road_IRI)
```

Measured on a real ten-query folder: 37 variables are bound in more than one
file, 8 of those with differing expression text, and skeleton comparison
reports 7 -- dropping the one case where two files feed an identical template
from differently-named columns. The gain is modest there because that folder's
column naming is fairly consistent; it grows with the number of files reading
the same concept under different column names.

The 7 kept included a literal typo -- `_Magnitude_LaneSegementTexture_`
against `_Magnitude_LaneSegmentTexture_` -- which had survived human review
precisely because it reads correctly at a glance.

**`TQL-002` and `TQL-003` are the same situation split by naming
convention.** TARQL binds every CSV header as a variable of the same name, so
"used in `CONSTRUCT`, not bound in `WHERE`" is the *normal* case: 32 of 228
variables in that same folder, exactly one of them a defect. A `?x_IRI` variable
is built rather than read, so an unbound one cannot be a column and is
reported as a Violation. Everything else is Info, and says plainly that the
CSV header is what settles it.

If your project uses a different convention for constructed variables, pass
your own suffixes to `bind_analysis.analyse(paths, constructed_suffixes=...)`.
The default is `("_IRI", "_iri", "_URI", "_uri")`.

### Query shape against the ontology (`CNF-001`..`CNF-005`)

Add `--ontology`, and the `CONSTRUCT` templates are rendered into a sketch
graph -- each variable becoming a placeholder IRI -- which is then diffed
against the ontology's declarations by exactly the code the `data` stage uses
on real triplified output.

```bash
ontology-quality-suite sketch --queries scripts/to_rdf \
  --ontology ontology/MergedOntologies.ttl --out-dir out/sketch
```

`CNF-001`/`CNF-002` are the cheapest useful signal in the whole suite: a
class or property the query builds that the ontology never declares, found
without a single row of CSV. Run it whenever the ontology changes and the
queries have not.

**If you get a flood of these, check the import closure first.** Every term
declared in an ontology your `--ontology` file fails to import is, by
definition, undeclared. Pass `--verbose` and read the import report before
believing a large `CNF-001`/`CNF-002` count. Since 0.8.0 an unresolved import
is reported as a warning in the default output, naming this exact
consequence.

### Prefix and namespace alignment

`TARQL_ALIGNMENT.md`, and `ontology-quality-suite consistency`, which
version-diffs an ontology, detects renames, and emits suggested repair diffs
against the queries rather than only findings.

### The real output

`triplify` then `data` -- the only stage that sees what the queries actually
produce from actual CSVs. `TARQL_ALIGNMENT.md` §"Reviewing real output data"
covers this.

## What none of this checks

Worth knowing before trusting a clean result:

- **CSV headers are never read.** Nothing here can tell a `TQL-003` finding
  that names a real column from one that names a typo. That is the reviewer's
  job, and it is why those are Info rather than Warning.
- **Expressions are compared, not evaluated.** `TQL-001` sees that two
  templates differ; it cannot tell you which is correct, or whether they
  happen to produce identical output for your actual data.
- **The `WHERE` clause is not otherwise analysed.** Joins, `OPTIONAL`
  blocks, `FILTER` conditions and `VALUES` are read only for the variable
  names they mention.
- **Dynamically built predicates are invisible to the `CNF` checks.** A
  predicate assembled with `IRI(CONCAT(...))` never appears literally in the
  `CONSTRUCT` template, so the sketch graph has a placeholder where the real
  IRI will be. The `data` stage catches these against real output; the
  `sketch` stage structurally cannot.
- **One query cannot drift against itself.** `TQL-001` needs a variable bound
  in at least two files. A single-query folder produces no drift findings, and
  that is not the same as being consistent.

## A review order that works

1. **`sketch` with no ontology.** Fast, needs nothing. Read
   `bind-review.txt` top to bottom -- it is ordered by how sure the findings
   are, and section 4 lists what is already consistent so you can see the
   check has actually looked.
2. **Fix every `TQL-002`.** An unbound constructed IRI is never correct.
3. **Adjudicate each `TQL-001`.** For each, one of the two expressions is
   right. Decide which, or rename the variables if the two genuinely mean
   different things.
4. **Spot-check `TQL-003` against the CSV headers.** Mostly columns; a typo
   here silently drops triples.
5. **`sketch --ontology`.** Now the query set is internally consistent, check
   it against the vocabulary. Confirm the import closure resolved before
   reading the counts.
6. **`triplify` then `data`.** Only once the cheap checks are clean, because
   everything above costs seconds and this costs a pipeline run.

## Reading `bind-review.txt`

```
1. Variables bound differently across files (7)
   ?texture_IRI  -- 2 patterns across 2 files
       tarql:expandPrefixedName(CONCAT("pnhd:_Magnitude_LaneSegementTexture_",?))
           tracscondition_to_rdf.tq:185
       tarql:expandPrefixedName(CONCAT("pnhd:_Magnitude_LaneSegmentTexture_",?))
           tracscategories_to_rdf.tq:113
```

Each variant carries its files and line numbers, and the two templates sit
one above the other, so the judgement can be made without opening either
file. Sections 2 and 3 list the unbound variables; section 4 lists the
variables bound in several files that agree, which is the part that tells you
the check ran and found things to compare.

## Wiring it into CI

```bash
# Fails the build on TQL-002 and CNF-003/CNF-004; TQL-001 and TQL-003 report
# without blocking.
ontology-quality-suite sketch --queries scripts/to_rdf \
  --ontology ontology/MergedOntologies.ttl \
  --out-dir out/sketch --fail-on Violation
```

`--fail-on` gates the exit code on severity. The default for `sketch` is
`never`, so add it explicitly. To promote or demote a check for your project,
copy the registry and edit `default_severity` -- see `PRIMER.md` §13 and
`EXTENDING.md`; the check keeps its id, prose and implementation.

## Adding your own query checks

`sketch/bind_analysis.py` is a worked example of a native check: query source
is not a graph, so there is nothing for a SPARQL or SHACL formulation to
match against. `EXTENDING.md` §3 covers the pattern -- add the `registry.json`
entry, return `ResultRow`s, wire it into the stage.
