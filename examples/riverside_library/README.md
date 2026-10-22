# Adding your own checks: the Riverside Library example

This example shows how to run the suite's standard checks over your data, and
then run **your own extra checks alongside them** without changing or copying
anything the suite ships.

Everything here is invented. Riverside Library does not exist, the namespace is
`example.org`, and the data is six records written to make four house rules
fire.

Every number and every command below was run against this folder. If you follow
the steps you should see the same figures.

---

## The idea in one paragraph

The suite finds its checks by **looking in directories**, not by reading a list.
So to add your own, you put `.rq` query files in a directory of your own and
name that directory as well as the suite's. Each check also needs an entry in a
**registry**, which is where its title, severity and fix-it advice come from.
The `--sparql` option can be given more than once, so query directories add up.
The `--registry` option can only be given once, so you need one file holding
both the suite's entries and yours. A small script builds that file.

---

## What is in this folder

```
examples/riverside_library/
├── README.md                        <- this file
├── merge_registry.py                <- builds the combined registry (see step 3)
├── ontology/
│   └── riverside-v1.ttl             <- the model: books, borrowers, loans
├── data/
│   └── catalogue.ttl                <- six invented records
└── house_checks/                    <- EVERYTHING THAT IS "OURS" LIVES HERE
    ├── registry-fragment.json       <- our four entries, and nothing else
    └── sparql/
        ├── structural/
        │   ├── LIB-001.rq           <- book with no ISBN          (Violation)
        │   └── LIB-002.rq           <- loan with no borrower       (Violation)
        ├── quality/
        │   └── LIB-003.rq           <- book with no shelf mark     (Warning)
        └── data/
            └── LIB-004.rq           <- book with zero copies held  (Info)
```

To do this in your own project, that `house_checks/` folder is the whole of
what you add. Nothing else on disk changes, and you never edit a file inside
`ontology_suite/`.

### The four house rules, and why each has the severity it has

| Check | Fires when | Severity | Why that level |
|---|---|---|---|
| `LIB-001` | a book has no `lib:isbn` | **Violation** | Riverside's ordering system keys on the ISBN. A title without one cannot be reordered. |
| `LIB-002` | a loan has no `lib:borrowedBy` | **Violation** | The book is out and nobody is recorded as having it. It can never be chased. |
| `LIB-003` | a book has no `lib:shelfMark` | **Warning** | The record still works. A borrower just cannot find the book on the shelf. |
| `LIB-004` | a book has `lib:copies 0` | **Info** | Nothing is wrong. The catalogue is correctly saying there are none. |

None of these is a general truth about libraries. They are Riverside's rules,
which is exactly what a house check is for.

---

## Step 1 — run the standard checks on their own

Start here, so you can see what the suite finds before you add anything.

```bash
uv run python -m ontology_suite.cli checks \
  --ontology examples/riverside_library/ontology/riverside-v1.ttl \
  --data examples/riverside_library/data/catalogue.ttl \
  --engine sparql --fail-on never \
  --out-dir out/riverside/default
```

Result:

```
Findings: 56 total (4 Violation, 35 Warning, 17 Info)
```

Those 56 come from ten of the suite's own checks. One of them is `QUA-001`,
"missing label", firing on `lib:Shelf` — the one class in the model that was
deliberately left without an `rdfs:label`, so that this step has something real
to report.

No `LIB-` findings appear, because we have not asked for them yet.

> **`--fail-on never` means "always exit 0".** It is used here so the command
> never stops the walkthrough. Step 5 covers what to use for a real build.

> **Two ways to type the command.** `uv run python -m ontology_suite.cli` is
> used throughout because it works from inside a clone of this repository. In
> your own project, where you have installed the suite with
> `pip install ontology-quality-suite`, the command is simply
> `ontology-quality-suite`. The options are identical either way:
>
> ```bash
> ontology-quality-suite checks --ontology … --data …
> ```

---

## Step 2 — read one check file

Open [`house_checks/sparql/structural/LIB-001.rq`](house_checks/sparql/structural/LIB-001.rq).
A check is an ordinary SPARQL `CONSTRUCT` query that builds one SHACL
validation result per problem it finds:

```sparql
CONSTRUCT {
  _:r a sh:ValidationResult ;
    sh:resultSeverity sh:Violation ;          # how serious
    sh:focusNode ?book ;                      # what is at fault
    sh:sourceConstraintComponent oq:LIB-001 ; # which check this is
    sh:resultMessage ?msg .                   # your own wording
}
WHERE {
  ?book a lib:Book .
  FILTER(isIRI(?book))
  FILTER NOT EXISTS { ?book lib:isbn ?anyIsbn }
  BIND(CONCAT("Book ", STR(?book), " has no lib:isbn. ...") AS ?msg)
}
```

Four things matter:

1. **`sh:sourceConstraintComponent` carries the id.** The suite reads the id
   from here, then looks it up in the registry to get the title and remediation.
2. **`sh:resultSeverity` is what actually counts.** It decides the Violation /
   Warning / Info tallies and whether a build fails.
3. **It must match `default_severity` in the registry.** The registry's copy is
   used for report badges and tags; the query's copy drives behaviour. If they
   disagree, your report says one thing and your exit code does another.
   Nothing checks this for you, so keep them in step by hand.
4. **`sh:resultMessage` is free text**, so the wording is entirely yours.

---

## Step 3 — build the combined registry

```bash
uv run python examples/riverside_library/merge_registry.py
```

```
wrote out\riverside\merged-registry.json
  61 checks from the installed suite
  4 from examples\riverside_library\house_checks\registry-fragment.json
  65 in total -- pass this file to --registry
```

The script reads the registry from **the suite you have installed** and adds our
fragment to it. That matters: when you upgrade the suite and it adds a check or
improves some advice, you get the new version automatically.

> **Do not copy the suite's `registry.json` and paste your entries into it.**
> It works today and rots silently. Your copy keeps the old 61 entries for ever,
> and nothing will tell you they have moved on. Keep your fragment holding only
> the checks you own, and merge at run time.

The script also refuses to run if one of your ids is already used by the suite,
because two entries sharing an id means whichever the lookup happens to reach
decides what your report says.

Run it again after any suite upgrade. It is cheap and repeatable.

---

## Step 4 — run the standard checks *and* yours

Two changes from step 1: point `--registry` at the merged file, and give
`--sparql` twice.

```bash
uv run python -m ontology_suite.cli checks \
  --ontology examples/riverside_library/ontology/riverside-v1.ttl \
  --data examples/riverside_library/data/catalogue.ttl \
  --registry out/riverside/merged-registry.json \
  --sparql examples/riverside_library/house_checks/sparql \
  --sparql @builtin \
  --engine sparql --fail-on never \
  --out-dir out/riverside/with_house
```

Result:

```
Findings: 60 total (6 Violation, 36 Warning, 18 Info)
```

Compared with step 1:

| | step 1, defaults only | step 4, with house rules |
|---|---|---|
| total | 56 | **60** |
| Violation | 4 | **6** &nbsp;(+ `LIB-001`, `LIB-002`) |
| Warning | 35 | **36** (+ `LIB-003`) |
| Info | 17 | **18** (+ `LIB-004`) |

All 56 original findings are still there. Four were added, one per rule, each on
exactly the record written to trip it:

```
LIB-001  Violation  .../book-02   Book missing an ISBN
LIB-002  Violation  .../loan-02   Loan does not record a borrower
LIB-003  Warning    .../book-03   Book has no shelf mark
LIB-004  Info       .../book-04   Book catalogued with no copies held
```

The titles in that last column came from the registry fragment, which is how you
can tell the merge worked.

### What `@builtin` means

`@builtin` is the suite's own query directory, wherever your installation put
it. Write that rather than the real path: the path differs between an editable
checkout and an installed package, so a path written into a committed config is
wrong on someone else's machine and wrong again after an upgrade.

### Where to put your query files

Any folder names will do — `structural/`, `quality/` and `data/` here simply
match the suite's own grouping. **One exception:** do not name a folder
`tarql`. That name is reserved for checks that need a special input, and the
suite skips such a folder in a normal run.

---

## Step 5 — make it fail a build

`--fail-on` sets the lowest severity that causes a non-zero exit. Measured on
this example:

| flag | exit code | meaning |
|---|---|---|
| `--fail-on Violation` | **1** | fails, because `LIB-001` and `LIB-002` are Violations |
| `--fail-on Warning` | **1** | fails, because there are Warnings too |
| `--fail-on never` | **0** | never fails, whatever is found |

`--fail-on Violation` is the usual choice for a pull-request gate.

> **Set each check's severity to what is true, then use `--fail-on` to decide
> what blocks a build.** It is tempting to label something a Warning so the
> build goes green. Resist it: severity is a statement about the finding, and
> once it is bent to manage a build nobody can compare two projects' reports.

---

## Two mistakes that are easy to make

Both were run for real, and both are quiet, which is what makes them worth
knowing.

### Mistake 1 — passing your own fragment to `--registry`

```bash
--registry examples/riverside_library/house_checks/registry-fragment.json
```

```
Findings: 58 total (6 Violation, 34 Warning, 18 Info)
```

Fifty-eight, not 60. `--registry` **replaces**, so the suite's 61 entries are
simply not there. The suite's checks still run — they are found by looking in
directories — but none of their ids resolves, so 54 findings are reported as
`UNMAPPED`, with no title and no remediation.

Worse, **two findings disappear**. Once every suite finding carries the same id
`UNMAPPED`, findings that differed only by which check produced them look
identical and collapse together. A configuration mistake quietly reduced the
count.

The fix is step 3: give `--registry` the merged file.

### Mistake 2 — forgetting `--sparql @builtin`

```bash
--sparql examples/riverside_library/house_checks/sparql
```

```
WARNING: --sparql replaced the suite's own query tree: 4 project check(s) ran
and 42 built-in check(s) did not. Pass --sparql @builtin as well to run both.
Findings: 4 total (2 Violation, 1 Warning, 1 Info)
```

Four findings, and a green-looking run that checked almost nothing. The suite
warns about this because a real project's CI gate was once found in exactly this
state. Note the warning goes to **stderr**, so a CI step that captures only
stdout will not show it.

---

## Checklist for your own project

1. Make one folder for your checks, with a `sparql/` tree inside it and a
   `registry-fragment.json` beside it.
2. Give your checks a prefix of your own — `LIB-`, `ACME-`, anything — so a
   reader can always tell your rules from the suite's.
3. Write one `.rq` per check. Copy `LIB-001.rq` as a starting point.
4. Add an entry per check to your fragment. All nine fields are needed: `id`,
   `category`, `metric`, `default_severity`, `title`, `description`,
   `remediation`, `cucumber_feature`, `cucumber_scenario`.
5. Keep each entry's `default_severity` equal to its query's
   `sh:resultSeverity`.
6. Copy `merge_registry.py` and change `FRAGMENT` to point at your fragment.
7. Run with `--registry <merged file>` and `--sparql <yours> --sparql @builtin`.
8. Choose `--fail-on` for your build.

If you would rather keep the settings in a file than type them each time, there
is a worked gate configuration — `ontology-suite.yml` plus a small `gate.py`
that reads it — in the `consolidated-ontology-suite-python-testing` repository.
It uses this same two-tree, merged-registry arrangement.
