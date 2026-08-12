# Worked example: Acme Robotics

`docs/PRIMER.md` is organized by task ("how do I check an ontology," "how
do I validate a CSV pipeline"), each with a small, isolated fixture. This
doc is the complement: one continuous, realistic worked example --
`examples/acme_robotics/` -- run through nearly every stage this suite has,
in the order a real project would actually hit them. Every command below
was run for real while writing this doc; the numbers are genuine output,
not illustrations.

Two runnable notebooks cover the same material with live, executable
cells: `docs/acme_robotics_lifecycle.ipynb` (this doc's §2-§6) and
`docs/acme_robotics_data_pipeline.ipynb` (§7-§8).

## 1. The fixture: a small org chart on real vocabularies

Rather than invent yet another toy domain, `examples/acme_robotics/`
extends two real, standards-track vocabularies -- fetched once and
committed under `examples/acme_robotics/reference_vocab/` so the whole
example runs offline:

| Vocabulary | Source | Used for |
|---|---|---|
| W3C Organization Ontology (`org:`) | `https://www.w3.org/ns/org#` | `org:OrganizationalUnit` as the base class for Acme's departments |
| FOAF (`foaf:`) | `http://xmlns.com/foaf/spec/index.rdf` | `foaf:Person` as the base class for Acme's employees; `foaf:name`/`foaf:mbox` |

`acme-org-v1.ttl` extends both (`owl:imports` both real files, resolved
locally via `--import-dir examples/acme_robotics/reference_vocab`) with
Acme-specific classes/properties -- and, in the same spirit as this
repo's own `examples/ontology/domain.ttl`, is **deliberately imperfect**,
each flaw commented with exactly which check it demonstrates:

| Term | Flaw | Check |
|---|---|---|
| `acme:Contractor` | `rdfs:subClassOf acme:Employee` **and** `owl:disjointWith acme:Employee` | `LOG-001` -- logically unsatisfiable |
| `acme:hasSkill` | no `rdfs:label` | `QUA-001`/`QUA-004` |
| `acme:reports_to` | local name isn't lowerCamelCase, no domain/range | `STY-002`, `STR-003` |

`acme-org-v2.ttl` is a plausible next release: fixes the ontology header's
metadata, renames `acme:Engineer` -> `acme:SoftwareEngineer` (a breaking
change, with an `owl:equivalentClass` migration annotation), and adds one
genuinely additive class+property. It deliberately leaves the
`Contractor`/`hasSkill`/`reports_to` issues untouched -- a real release
doesn't fix everything at once.

`employees.csv` + `employees.rq` (a TARQL/oxi-gen CONSTRUCT query) plus
`taxonomy.ttl` (a small SKOS-based controlled department list) round out
the CSV-to-RDF pipeline and taxonomy-governance story used in §7-§8.

## 2. Ontology-only quality gate (`ontology`)

The tightest loop -- no data, just "is the TBox itself sound":

```bash
ontology-quality-suite ontology --ontology examples/acme_robotics/acme-org-v1.ttl \
  --import-dir examples/acme_robotics/reference_vocab --out-dir out/acme-ontology
```

This runs the always-on `owlrl` closure + pattern checks *and* (with `uv
sync --extra reasoner` installed, and a working HermiT/Java setup) a real
external DL reasoner. The always-on pass alone already catches the
deliberate contradiction:

```
LOG-001   pattern-based (owlrl closure + SPARQL)  -- Contractor disjoint with its own ancestor Employee
```

When the external reasoner runs cleanly, it independently confirms the
same finding twice more -- `REA-020` ("the ontology is inconsistent") and
`REA-021` ("Contractor is unsatisfiable, equivalent to owl:Nothing"),
three separate confirmations of one root cause, exactly the point of
running a rule-based closure and a real reasoner together. HermiT is
occasionally environment-flaky in ways unrelated to this fixture (a
transient internal error rather than a real unsatisfiability finding) --
when that happens, the suite degrades gracefully to a `REA-022`
("reasoner unavailable") info-level note instead of failing the whole run,
and `LOG-001` alone is still enough to catch this ontology's real problem.
See `docs/REASONING.md` for the full reasoner-selection story (`--reasoner
pellet` is the more robust choice on ontologies HermiT specifically
struggles with, e.g. this repo's own `examples/ontology/domain.ttl` and
its `xsd:date` property).

## 3. The full registry, and "whose problem is this?" (`checks`)

```bash
ontology-quality-suite checks --ontology examples/acme_robotics/acme-org-v1.ttl \
  --import-dir examples/acme_robotics/reference_vocab --engine sparql \
  --out-dir out/acme-checks --fail-on never
```

With the real `org:`/`foaf:` imports resolved, this reports close to 300
findings (Warning/Info hold steady at 162/84 across runs; the Violation
count varies by a few from run to run -- a real, minor pre-existing
nondeterminism somewhere in how a handful of checks evaluate the merged
import graph, independent of anything in this walkthrough) -- but only a
handful are Acme's own; the rest are the registry's full ~50-check pass
finding real, pre-existing documentation/style gaps *inside the W3C
Organization Ontology and FOAF themselves*. That's not a bug in the
"checking the wrong thing" sense -- the suite is correctly checking
everything in the merged graph -- but it's not a useful CI signal for "did
my change introduce a new problem."

Two ways to narrow it, with a real tradeoff between them:

```bash
# Blunt: re-run without resolving imports at all.
ontology-quality-suite checks --ontology examples/acme_robotics/acme-org-v1.ttl \
  --exclude-imports --engine sparql --out-dir out/acme-checks-excluded --fail-on never
```

This drops to 9 findings, but reintroduces its own noise: with FOAF's
triples gone, `foaf:Person`/`foaf:0.1/` themselves become "unresolved
reference target" findings (`DAT-002`, `QUA-004`) -- a different kind of
false signal, not Acme's own problem either.

```bash
# Precise: keep imports resolved, filter the report by IRI prefix instead.
ontology-quality-suite checks --ontology examples/acme_robotics/acme-org-v1.ttl \
  --import-dir examples/acme_robotics/reference_vocab --engine sparql \
  --own-namespace https://acme.example.org/ --out-dir out/acme-checks-own --fail-on never
```

This gives exactly **5 findings**, all genuinely Acme's own, with the
imports still fully resolved (so a real domain/range check against an
imported property still fires if it should -- `--exclude-imports` can't
say that):

```
LOG-001  Violation  acme:Contractor       -- disjoint with its own ancestor
QUA-001  Warning    acme:hasSkill         -- no rdfs:label
QUA-004  Warning    acme:hasSkill         -- no skos:prefLabel either
STR-003  Warning    acme:reports_to       -- no rdfs:domain/range
STY-002  Warning    acme:reports_to       -- local name not lowerCamelCase
```

`--own-namespace` filters the *report*, not what gets checked -- imports
stay resolved and reasoned over normally. Use it as your everyday
local/CI lint of what *you* changed; run the import-inclusive pass
periodically (or when an imported vocabulary itself changes) as a broader
audit.

**Which engine?** `--engine` defaults to `native+sparql` if the optional
Rust SHACL engine is installed, else `both` (pyshacl). See
`docs/ARCHITECTURE.md`'s "Choosing which engine actually runs" for the
full comparison.

## 4. Adding a project-specific check

`examples/acme_robotics/custom_checks/` demonstrates `docs/EXTENDING.md`'s
walkthrough with a real addition: `sparql/structural/ACM-001.rq` --
every `acme:Employee` must carry an `acme:hasEmployeeId`, Acme's own
HR-traceability requirement that a general-purpose registry has no reason
to know about. It's a minimal, self-contained registry (just this one
check) rather than a full copy of `ontology_suite/resources/` -- point
`--shapes`/`--sparql` at the real resources dir too if you want the full
registry to keep running alongside your own additions.

```bash
ontology-quality-suite checks --ontology examples/acme_robotics/acme-org-v1.ttl \
  --data <triplified employee data -- see §7> \
  --registry examples/acme_robotics/custom_checks/registry.json \
  --sparql examples/acme_robotics/custom_checks/sparql \
  --engine sparql --out-dir out/acme-acm001
```

Verified against real triplified data (§7): **0 false positives** against
Acme's five real, complete employee records; **fires correctly** against a
deliberately incomplete record missing `acme:hasEmployeeId`. A project's
own rules, versioned in Git alongside the ontology, with zero changes
needed to the installed package.

## 5. Reference documentation (`docgen`)

```bash
ontology-quality-suite docgen --ontology examples/acme_robotics/acme-org-v1.ttl \
  --out-dir out/acme-docgen
```

Produces a self-contained `ontology-documentation.html` (class/property
tables, Mermaid diagrams, one Graphviz concise-bounded-description diagram
per class) -- verified: 4 classes, 2 object properties, 3 datatype
properties, correctly listing `foaf:Person`/`org:OrganizationalUnit` as
unresolved external terms (expected -- pass `--ref` pointing at the real
vocab files to resolve them into the doc too).

## 6. Versioning, drift, and auto-repair

### `version-diff`: evidence-based semver

```bash
ontology-quality-suite version-diff examples/acme_robotics/acme-org-v1.ttl \
  examples/acme_robotics/acme-org-v2.ttl --out-dir out/acme-version-diff --json
```

Correctly classifies v2 as **MAJOR** (the `Engineer` removal) while also
reporting the purely additive changes (`ProductManager`, `hireDate`) as
minor:

```
Removed classes [MAJOR]:      acme:Engineer
Added classes [minor]:        acme:ProductManager, acme:SoftwareEngineer
Added properties [minor]:     acme:hireDate
Added equivalentClass axioms: acme:Engineer equivalentClass acme:SoftwareEngineer
Suggested version bump: MAJOR
```

### `consistency`: does this release break my TARQL queries, and can it fix itself?

`employees.rq` types every employee `acme:Engineer` -- the exact term v2
renames:

```bash
ontology-quality-suite consistency --new examples/acme_robotics/acme-org-v2.ttl \
  --old examples/acme_robotics/acme-org-v1.ttl --queries examples/acme_robotics \
  --import-dir examples/acme_robotics/reference_vocab --out-dir out/acme-consistency
```

```
1 class(es) used in TARQL but not declared in the ontology set:
  [undeclared_class] https://acme.example.org/ns/Engineer

1 suggested repair(s):
  [rename_iri] employees.rq (confidence 100%)
    Update to the renamed ontology term(s): acme:Engineer -> acme:SoftwareEngineer
```

Note what *isn't* needed above: just `--import-dir`, no repeatable
`--ontology <path>` pointing at `org.ttl`/`foaf.rdf` by hand --
`consistency` resolves `--new`'s `owl:imports` for the TARQL-alignment
half the same way it always did for the version-diff half (see
`docs/CONSISTENCY_AND_REPAIR.md`'s "`--import-dir` covers both halves of
the check").

Dry-run writes `.patch` files under `--out-dir/repairs` for review.
`--apply-repairs --min-confidence 0.7` applies them directly -- verified:
`employees.rq`'s `acme:Engineer` is correctly rewritten to
`acme:SoftwareEngineer` in place.

**The migration annotation, either direction.** `acme-org-v2.ttl` asserts
`acme:Engineer owl:equivalentClass acme:SoftwareEngineer` -- old
(removed) IRI to new (added) one, the conventional direction. Since
`owl:equivalentClass` is logically symmetric, the reverse direction
(`acme:SoftwareEngineer owl:equivalentClass acme:Engineer`, arguably the
more natural way to write "here's the new term and what it replaces") is
recognized just as reliably, at the same full confidence -- both were
verified directly against this fixture while building this example. See
`ontology_suite/versioning/rename_detection.py`'s module docstring for why
`dcterms:isReplacedBy`, unlike this predicate, stays one-way.

## 7. The CSV-to-RDF pipeline

Cheapest check first:

```bash
# 1. Does the query even reference vocabulary the ontology declares? No CSV needed.
ontology-quality-suite sketch --queries examples/acme_robotics \
  --ontology examples/acme_robotics/acme-org-v1.ttl \
  --import-dir examples/acme_robotics/reference_vocab --out-dir out/acme-sketch

# 2. Actually triplify (needs a built oxi-gen binary -- see config.find_oxi_gen_binary).
ontology-quality-suite triplify --csv-dir examples/acme_robotics --queries examples/acme_robotics \
  --out-dir out/acme-triplify

# 3. Assess what it actually produced.
ontology-quality-suite data out/acme-triplify/employees.ttl \
  --ontology examples/acme_robotics/acme-org-v1.ttl \
  --import-dir examples/acme_robotics/reference_vocab \
  --engine sparql --own-namespace https://acme.example.org/ \
  --out-dir out/acme-data --fail-on never
```

Step 2, verified against real `oxi-gen`, correctly triplifies all 5
employees (`foaf:name`, `foaf:mbox`, `acme:worksIn`, `acme:hasSkill`,
...). Step 3, checked against the real FOAF import: **zero
`CNF-003`/`CNF-004` domain/range findings against `foaf:name`/`foaf:mbox`**
-- worth calling out because FOAF deliberately declares `foaf:name`'s
`rdfs:domain` as `owl:Thing` (its intentional "usable on absolutely
anything" convention), and a naive `rdfs:subClassOf` ancestor-walk can
never discover that every class is implicitly a subtype of `owl:Thing`
under OWL semantics -- that's an axiomatic fact, not something asserted as
an ordinary triple. `check_conformance` special-cases `owl:Thing` and
`rdfs:Resource`/`rdfs:Literal` as always-satisfied for exactly this
reason.

For a data graph too large to reason over in full, sample the reasoning
pass while still checking the full (cheap) registry over everything:
`--sample 5000` on `data`.

## 8. The taxonomy boundary (`pattern-consistency`)

§6's `consistency` catches ontology<->transformation drift. It has no
notion of a *taxonomy* layer -- a controlled vocabulary of values (SKOS
concepts, `org:`-style categories) sitting on top of the ontology.
`pattern-consistency` checks that boundary, plus the ones around it,
together:

```bash
ontology-quality-suite pattern-consistency \
  --queries examples/acme_robotics/employees.rq \
  --ontology examples/acme_robotics/acme-org-v1.ttl \
  --ontology examples/acme_robotics/reference_vocab/org.ttl \
  --ontology examples/acme_robotics/reference_vocab/foaf.rdf \
  --taxonomy examples/acme_robotics/taxonomy.ttl \
  --out-dir out/acme-pattern-consistency
```

Result on this fixture: **clean** -- correctly, not a false negative.
`employees.csv` has a row (`E005`) whose department, `MKT`, isn't one of
`taxonomy.ttl`'s three declared departments (`ENG`/`QA`/`SALES`). But the
taxonomy<->transformation check specifically looks for a value **hard-coded
directly in the query template's text**; `employees.rq`'s department is
built dynamically, per CSV row (`BIND(IRI(CONCAT(...,?department)) AS
?dept)`) -- there's no fixed literal in the query text to flag.

Passing the real triplified output too closes exactly this gap --
`check_taxonomy_membership` checks *values actually used in the data*
against the taxonomy, not the query template:

```bash
ontology-quality-suite pattern-consistency \
  --queries examples/acme_robotics/employees.rq \
  --ontology examples/acme_robotics/acme-org-v1.ttl \
  --ontology examples/acme_robotics/reference_vocab/org.ttl \
  --ontology examples/acme_robotics/reference_vocab/foaf.rdf \
  --taxonomy examples/acme_robotics/taxonomy.ttl \
  --output-data out/acme-triplify/employees.ttl \
  --out-dir out/acme-pattern-consistency-with-data
```

```
== taxonomy <-> output data ==
  [undeclared_taxonomy_reference] https://acme.example.org/data/department/MKT is used as
  the value of https://acme.example.org/ns/worksIn in the data graph but is not declared
  as an individual anywhere in the given taxonomy set.
```

No new flag beyond `--output-data`, which `pattern-consistency` already
accepts for the sibling ontology<->output-data conformance check -- see
`docs/MODELLING_PATTERN_CONSISTENCY.md`'s "The gap `check_taxonomy_references`
itself can't cover: dynamically-built values" for the full mechanism,
including how the property-to-taxonomy-class binding (`acme:worksIn` ->
`acme:Department`) is inferred automatically rather than needing to be
configured by hand.

## 9. Live triplestore checking (`consistency-remote`)

Once Acme's org chart moves from Git-tracked Turtle to a real triplestore,
`consistency-remote` runs the same three-way consistency check against it
instead of local files:

```bash
ontology-quality-suite consistency-remote \
  --query-endpoint http://localhost:3030/acme/sparql --manifest manifest.json \
  --auth-user admin --auth-password secret --out-dir out/acme-consistency-remote
```

`manifest.json` binds a named graph URI to its role (`ontology` /
`triplified_data`) and, for data graphs, which local query produced it and
which ontology graph it should conform to -- see
`docs/acme_robotics_data_pipeline.ipynb` for a live, runnable version
against a real in-process SPARQL server (no Docker/Fuseki install
required to follow along), and `docs/FUSEKI.md` for the full manifest
spec and the Python API (`remote.fuseki`) for anything beyond the
three-way consistency check.

## Where to go from here

- **Runnable notebooks**: `docs/acme_robotics_lifecycle.ipynb` (§2-§6) and
  `docs/acme_robotics_data_pipeline.ipynb` (§7-§9), validated on every push
  by `.github/workflows/notebook.yml`, same as `docs/primer.ipynb`.
- **This suite's own docs** (`docs/`): `PRIMER.md` for the general
  task-oriented guide this walkthrough assumes; `ARCHITECTURE.md` for the
  `--engine` deep-dive; `EXTENDING.md` for the full check-authoring
  walkthrough; `FUSEKI.md` for the live-triplestore API;
  `CONSISTENCY_AND_REPAIR.md` for the full finding-kind -> fix-kind ->
  confidence table behind §6's auto-repair; `MODELLING_PATTERN_CONSISTENCY.md`
  for §8's four-boundary model in full.
- **The worked example itself**: `examples/acme_robotics/` -- real files,
  not excerpts; every command above runs against them as shown.
