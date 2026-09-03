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

## The five things that go wrong

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

**4. A variable is bound, but to the wrong kind of thing.** The suffix
conventions make a promise the expression has to keep. `?x_IRI` should be an
IRI, and `CONCAT` returns a string -- TARQL does not coerce it, so the triple
is dropped exactly as in (3), except the query looks *more* correct because
the variable is bound. `?x_DT` should carry a datatype, and if it does not
the triple loads perfectly well carrying an untyped string, which surfaces
much later as a range violation or a comparison where `"10"` sorts before
`"9"`. Caught by `TQL-004` and `TQL-005`.

**5. The query builds a shape the ontology forbids.** A property used outside
its declared domain, a value outside its range. Caught by `CNF-003`/`CNF-004`.

## The checks, cheapest first

Everything below runs from one command:

```bash
ontology-quality-suite sketch --queries queries/ --out-dir out/sketch
```

`--ontology` is optional. Without it you still get the whole `TQL-00x` half,
because that half reads the query source rather than comparing against
anything.

### Query source review (`TQL-001` .. `TQL-005`)

No ontology, no CSV, no triplification. `sketch/bind_analysis.py` parses each
query's `BIND` statements and `CONSTRUCT` template and writes
`out/sketch/bind-review.txt`, plus `out/sketch/bind-facts.ttl` -- the same
facts as RDF, which is what lets `TQL-004` and any check you write yourself
be a query file rather than Python. See *Adding your own query checks*.

| Check | Severity | Fires when |
|---|---|---|
| `TQL-001` | Warning | One variable is bound by structurally different expressions in different files |
| `TQL-002` | Violation | A `?something_IRI` variable is used in `CONSTRUCT` but never bound |
| `TQL-003` | Info | Any other `CONSTRUCT` variable is unbound -- probably a CSV column, worth confirming |
| `TQL-004` | Violation | A `?something_IRI` variable is bound to something that is not an IRI |
| `TQL-005` | Warning | A `?something_DT` variable is bound to something that carries no datatype |

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

One of the 7 was a genuine defect. Two files spelled the same IRI template
differently by a single character buried in a long name, and reviewers had
read past it more than once. That is exactly the case this check exists for:
a difference small enough to be invisible, and large enough to mint two IRIs
for one thing.

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

That setting governs `TQL-002`/`TQL-003` only. `TQL-004` and `TQL-005` read
their suffixes from their own query text, which is the point of their being
query files: if your project spells the datatype convention `_TYPED` rather
than `_DT`, change the `STRENDS` in a copy of `TQL-005.rq` and point
`--sparql` at it. No code, and no argument with anyone else's convention.

### Query shape against the ontology (`CNF-001`..`CNF-005`)

Add `--ontology`, and the `CONSTRUCT` templates are rendered into a sketch
graph -- each variable becoming a placeholder IRI -- which is then diffed
against the ontology's declarations by exactly the code the `data` stage uses
on real triplified output.

```bash
ontology-quality-suite sketch --queries queries/ \
  --ontology ontology/merged-ontology.ttl --out-dir out/sketch
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
1. Variables bound differently across files (1)
------------------------------------------------------------
   ?road_IRI  -- 2 patterns across 2 files
       tarql:expandPrefixedName(CONCAT("exd:_Road_", ?))
           roads_to_rdf.rq:17
       tarql:expandPrefixedName(CONCAT("exd:_Road_", REPLACE(?, " ", "_")))
           lanes_to_rdf.rq:17
```

(That is the real output of `examples/tarql_drift/`, this repo's own two-query
fixture -- `ontology-quality-suite sketch --queries examples/tarql_drift` will
reproduce it.)

Each variant carries its files and line numbers, and the two templates sit
one above the other, so the judgement can be made without opening either
file. Sections 2 and 3 list the unbound variables; section 4 lists the
variables bound in several files that agree, which is the part that tells you
the check ran and found things to compare.

## Wiring it into CI

```bash
# Fails the build on TQL-002 and CNF-003/CNF-004; TQL-001 and TQL-003 report
# without blocking.
ontology-quality-suite sketch --queries queries/ \
  --ontology ontology/merged-ontology.ttl \
  --out-dir out/sketch --fail-on Violation
```

`--fail-on` gates the exit code on severity. The default for `sketch` is
`never`, so add it explicitly. To promote or demote a check for your project,
copy the registry and edit `default_severity` -- see `PRIMER.md` §13 and
`EXTENDING.md`; the check keeps its id, prose and implementation.

## Adding your own query checks

Every check in this suite is a file plus a registry entry, and TARQL checks
are no exception -- including the ones you write for your own project,
outside this package, without forking it.

That needs explaining, because for a long time it was not true. A SPARQL or
SHACL check works that way because the thing being checked is a *graph*: the
runner executes the file and reads `sh:ValidationResult` back out,
understanding nothing about what the check means. Query source is not a
graph. `sketch.ttl` holds each query's `CONSTRUCT` template with the `WHERE`
clause thrown away, so by the time it exists every `BIND` is gone -- which is
why `TQL-001`, `TQL-002` and `TQL-003` are Python.

So the run publishes the query source as its own graph, and a check over
*that* is an ordinary `.rq` file.

### `bind-facts.ttl`

Written to your `--out-dir` on every sketch run, beside `bind-review.txt`.
One node per `BIND`, one per `CONSTRUCT` variable, one per file:

```turtle
@prefix tq:  <https://semantechs.co.uk/ontology-quality/tarql/> .
@prefix tqd: <https://semantechs.co.uk/ontology-quality/tarql/data/> .

tqd:roads_to_rdf.rq a tq:Query ;
    tq:source "roads_to_rdf.rq" ;
    tq:path   "/abs/path/to/roads_to_rdf.rq" .

tqd:roads_to_rdf.rq/bind/17 a tq:Bind ;
    tq:inQuery    tqd:roads_to_rdf.rq ;
    tq:target     "road_IRI" ;
    tq:expression "tarql:expandPrefixedName(CONCAT(\"exd:_Road_\", ?roadid))" ;
    tq:skeleton   "tarql:expandPrefixedName(CONCAT(\"exd:_Road_\", ?))" ;
    tq:source     "roads_to_rdf.rq" ;
    tq:line       17 .

tqd:roads_to_rdf.rq/var/roadname a tq:ConstructVariable ;
    tq:inQuery     tqd:roads_to_rdf.rq ;
    tq:variable    "roadname" ;
    tq:bound       false ;
    tq:constructed false ;
    tq:source      "roads_to_rdf.rq" .
```

| Class | Predicates |
|---|---|
| `tq:Query` | `tq:source` (basename), `tq:path` (as given) |
| `tq:Bind` | `tq:target`, `tq:expression`, `tq:skeleton`, `tq:line`, `tq:source`, `tq:inQuery`, `tq:outermostCall`, `tq:producesKind` |
| `tq:ConstructVariable` | `tq:variable`, `tq:bound`, `tq:constructed`, `tq:source`, `tq:inQuery` |

Two things are worth knowing about this vocabulary rather than discovering.

**`tq:skeleton` is the expression with every `?var` replaced by `?`.** It is
computed in Python because it is a *parse*, and SPARQL cannot do it. Once it
is a literal in the graph, asking whether two files disagree about one is a
`GROUP BY`. That is the division of labour the whole mechanism rests on:
Python parses, SPARQL asks.

**Both bound and unbound variables are published.** Emitting only the gaps
would make "what does this query actually bind" unwritable, and half the
useful questions are of that shape.

**`tq:producesKind` and `tq:outermostCall` are parse results, not text
searches.** The outermost call is the one wrapping the whole expression --
`CONCAT("x", IRI(?a))` is a `CONCAT`, not an `IRI` -- which needs the bracket
matching the parser already did.

`tq:producesKind` says what kind of RDF term the expression yields:

| Kind | From |
|---|---|
| `IRI` | `IRI()`, `URI()`, `tarql:expandPrefixedName()` |
| `TypedLiteral` | `STRDT()`, or a literal written `"..."^^xsd:type` |
| `LangLiteral` | `STRLANG()`, or a literal written `"..."@en` |
| `String` | `CONCAT()`, `STR()`, `REPLACE()`, `SUBSTR()`, … or a bare `"..."` |
| *(absent)* | a bare variable, or a function this suite does not recognise |

**Absent, never guessed** -- a bare `BIND(?x AS ?y_IRI)` may be passing on an
IRI from an earlier `BIND`. That is what lets a check reading this fact carry
Violation severity instead of hedging.

It is a *kind* rather than a boolean for a reason worth borrowing. The first
version published `tq:producesIri`, a yes/no. `STRDT` was filed with the
string functions, which answered the `_IRI` question correctly by accident --
a typed literal is not an IRI either -- and made the `_DT` question
unaskable. A fact that happens to answer the one question asked of it is the
kind that silently blocks the next one.

Node IRIs come from the file's basename and the line, so a finding's focus
node is stable across runs and reads as the place to open -- which is what a
query-source finding has instead of a subject IRI.

### Writing the check

A `CONSTRUCT` that emits `sh:ValidationResult`, exactly like every other
check in the suite. This one is the house rule "every minted IRI goes through
`tarql:expandPrefixedName`":

```sparql
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX oq: <https://semantechs.co.uk/ontology-quality/>
PREFIX tq: <https://semantechs.co.uk/ontology-quality/tarql/>

CONSTRUCT {
  _:r a sh:ValidationResult ;
    sh:resultSeverity sh:Warning ;
    sh:focusNode ?bind ;
    sh:resultPath tq:expression ;
    sh:value ?expression ;
    sh:sourceConstraintComponent oq:TQL-900 ;
    sh:resultMessage ?msg .
}
WHERE {
  ?bind a tq:Bind ;
        tq:target ?target ; tq:expression ?expression ;
        tq:source ?source ; tq:line ?line .
  FILTER(STRENDS(?target, "_IRI"))
  FILTER(!CONTAINS(?expression, "expandPrefixedName"))
  BIND(CONCAT("?", ?target, " (", ?source, ":", STR(?line),
              ") does not use the agreed IRI template.") AS ?msg)
}
```

`sh:sourceConstraintComponent oq:<id>` is what ties the finding back to its
registry entry; without it the row arrives with no title, severity or
remediation. `sh:focusNode ?bind` is what makes the finding point at a line
in a file.

### The one rule worth internalising

**If a `FILTER` is trying to recover structure from the expression text, stop
and add a predicate instead.**

The line between Python and SPARQL here is *not* difficulty. `TQL-001`'s
cross-file comparison is a nested aggregate with two `COUNT DISTINCT`s and a
compound `HAVING` -- harder SPARQL than anything in `TQL-004` -- and it works
fine as a query, because the skeleton it compares is already a fact. The line
is whether the check needs to *understand* the query text or merely to
*relate* things already parsed out of it.

`TQL-004` is the worked example of getting this wrong and then right. Its
first draft asked whether the expression contained `"IRI("`. That reads
`MYIRI(` as a conversion, misses `BIND("exd:_Constant_" AS ?x_IRI)` entirely,
and cannot tell a nested `IRI()` from the outermost call. All three are
questions about *structure*, and the parser had already answered them --
`_matching` had to bracket-match to find the expression at all. So
`produces_iri` decides it once in Python, publishes `tq:producesIri`, and the
whole check becomes one triple pattern.

Adding a predicate is nearly always the better change: it is smaller than a
native check, and every check written afterwards can use it.

Two other practical notes, both learned the hard way writing `TQL-004`:

- **rdflib's SPARQL parser rejects a single-quoted string containing a double
  quote** (`'"'`), and SPARQL string literals only permit a fixed set of
  backslash escapes -- `\s` in a `REGEX` is a parse error, not a regex. Prefer
  `CONTAINS`/`STRENDS` over `REGEX` where you can.
- **Scope the check so it is actionable on sight.** `TQL-004` fires only
  where `tq:producesIri` is explicitly `false`, never where it is absent. A
  check that is right most of the time gets muted, and then it is worth
  nothing.

### The manifest entry

Every check needs one, whether it lives here or in your project. The fields
are the same nine every other check uses -- there is no separate TARQL
manifest:

```json
{
  "id": "TQL-900",
  "category": "tarql",
  "metric": "project IRI conventions",
  "default_severity": "Warning",
  "title": "IRI minted outside the house template",
  "description": "A ?x_IRI variable is built without tarql:expandPrefixedName, so the IRI it mints will not match the ones every other query in this project produces.",
  "remediation": "Wrap the expression in tarql:expandPrefixedName().",
  "cucumber_feature": "TARQL Query Consistency",
  "cucumber_scenario": "Every minted IRI uses the agreed template"
}
```

| Field | What it is for |
|---|---|
| `id` | Must match `sh:sourceConstraintComponent oq:<id>` and the `.rq` filename. Use your own prefix (`ACME-001`) if you would rather not extend `TQL-`. |
| `category` | Groups the check in every report. `tarql` puts it with the query-source findings. |
| `metric` | One phrase naming what is measured. Shown under the title. |
| `default_severity` | `Violation`, `Warning` or `Info`. Overridable per run. |
| `title` | One line, shown in the Problems list and the summary tables. |
| `description` | What the check asserts and why it matters. This is what a reader consults when they disagree with a finding, so say what is *out* of scope too. |
| `remediation` | What to do about it. Written as an instruction. |
| `cucumber_feature` / `cucumber_scenario` | Names in the generated `cucumber.json` and `.feature` files. |

### Running it from your own tree

No fork and no code. Put the query and the manifest wherever your project
keeps them:

```
my-checks/
  registry.json                 # your entries (start from a copy of the suite's)
  sparql/
    tarql/
      TQL-900.rq
```

```bash
ontology-quality-suite sketch --queries queries/ \
  --registry my-checks/registry.json \
  --sparql   my-checks/sparql \
  --out-dir  out/sketch
```

**`--sparql` replaces the tree, it does not add to it** -- the same as
everywhere else in the suite. If you want the built-in checks as well as your
own, copy the suite's `sparql/` directory as your starting point rather than
starting from an empty one. `--registry` behaves the same way, which is why
the layout above starts from a copy.

Checks in `sparql/tarql/` run against the BIND facts graph and nothing else.
They are held back from the ontology and data sweeps deliberately: a query
that runs against every graph and matches almost everywhere is
indistinguishable from one that has quietly stopped working.

### What still needs Python

Anything that has to *parse*. Skeletonisation and `produces_iri` are the two
standing examples: both are simple to describe and impossible to do correctly
with string functions, because they need to know where a call ends and
whether a `#` or a bracket is inside a literal.

If you want a new derived fact about an expression, it belongs in
`bind_analysis.py`, gets published as another predicate on `tq:Bind`, and is
queryable from then on. `EXTENDING.md` §3 covers writing a native check;
adding a predicate is usually the smaller and more reusable change, and it
leaves the check itself readable.
