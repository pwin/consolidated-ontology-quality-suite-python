# Reports

Every report-writing subcommand writes the same set of files into `--out-dir`.
There is one file per audience and no file that restates another.

| File | Who reads it |
|---|---|
| `findings.txt` | a person at a terminal, or an agent — what, where, and the source line |
| `full_results.csv` | a script — the complete record, one row per finding, every field |
| `cucumber.json` | a CI plugin — every check as passed / pending / failed |
| `features/*.feature` | the same, as Gherkin text, one file per category |
| `report.html` | a browser — summaries, plots, and the first 500 findings |
| `plots/*.png` | embedded by `report.html` |

`findings.txt` and `full_results.csv` carry the same finding set. If they ever
disagree on the count, that is a bug — neither is a sample.

## What changed in 0.15.0

Six files stopped being written: `summary_by_category`, `summary_by_check` and
`top_offenders`, each as both `.csv` and `.md`. Every one was a `groupby` over
`full_results.csv`, written on every run of every subcommand, and nothing in
this repo, its notebooks or its docs read any of them.

The aggregates themselves are unchanged and still on show — `report.html`
renders all three as tables and `plots/` draws them. What went was six files
nobody opened, and the cost of them was never the disk: an `out/` directory of
thirteen files gives no clue which one to start with.

`findings.txt` is new, and `full_results.csv` gained `source_file` and `line`.

## findings.txt

```
480 finding(s): 55 Violation, 341 Warning, 84 Info

-- located: 25 finding(s) at 10 place(s) -----------------------------------

ontology/acme-org-v1.ttl

    45 |     rdfs:comment "An employee whose primary role is engineering."@en .
    46 |
  > 47 | acme:Contractor a owl:Class ;
    48 |     rdfs:subClassOf acme:Employee ;
    49 |     owl:disjointWith acme:Employee ;

      47  LOG-001   Violation https://acme.example.org/ns/Contractor is disjoint with
                              one of its own transitive superclasses.

-- not located: 455 finding(s) ---------------------------------------------

  STR-002  Violation  Undefined property used  (5)
      A predicate used on {} is never declared as rdf:Property/owl:ObjectProperty...
        http://www.w3.org/ns/org#  (path http://purl.org/dc/terms/contributor)
        http://www.w3.org/ns/org#  (path http://purl.org/dc/terms/created)

-- how to fix --------------------------------------------------------------

  LOG-001  Violation  Class disjoint with its own ancestor  (1)
      Remove either the subclass axiom or the disjointness axiom.
```

Three things are arranged to be said once:

- **a source line is quoted once**, however many checks fired on it. On this
  repo's worked example 15 of 25 located findings shared a line with another.
  Nearby positions share one extract rather than quoting overlapping windows.
- **a template message is printed once per check.** `"<X> has no rdfs:label"`
  under twenty findings is one sentence repeated twenty times with one word
  changing — and that word is the line beneath it. A message carrying anything
  the fields do not is still printed per finding.
- **a remediation is printed once per check**, under `how to fix`, which
  doubles as the index of every check that fired.

Nothing is capped or sampled, and no two lines read alike unless they describe
the same finding: when two findings would render identically, the fields that
tell them apart are added to both.

### Where positions come from

Two routes, and they differ in how far they can be trusted.

**Exact.** TARQL checks (`TQL-*`) get theirs from the query parser, which knows
which line the `BIND` was on. `bind_analysis` publishes it as `tq:line` and
`tq:path`, and `TQL-004`/`TQL-005` carry it into the result as
`oq:sourceFile`/`oq:sourceLine`. Any check that knows a position can do the
same — emit those two properties on the validation result and the row will
carry them.

**Best-effort.** Ontology findings are graph-shaped: rdflib discards source
position at parse time, so by the time a check runs, *which line* is no longer
a question the data can answer. `checks/locate.py` recovers it by searching the
file for the line that declares the focus node, and answers nothing when it
cannot settle the question — a blank node, a term declared in an import, a term
written only as an object. Those findings appear under **not located**, with
their focus node instead of a position.

A large *not located* count is usually not a fault. On the example above all
455 were FOAF, Dublin Core and W3C terms: findings about somebody else's
vocabulary, which is the cue to reach for `--own-namespace`.

## Organising by question: `--themes`

`--themes <file>` adds a **by question** index to the top of `findings.txt`,
grouping the checks that fired under questions written in your own words rather
than under check ids.

```
-- by question -------------------------------------------------------------

  A check can be evidence for more than one question, so these counts
  overlap and do not sum to the total above.

  Missing preferred label  (109)
      QUA-004 x20, QUA-009 x89

  not mapped to a question  (281)
      DAT-002, DAT-003, LOG-001, QUA-001, ...
```

It is an index, not a fourth listing: every finding is already printed once,
under its place or its check. What it adds is the triage order and the check
ids to grep for.

The map is an input rather than registry data, for the same reason project
checks are loaded through `--registry` and `--sparql` rather than by forking
the suite: the grouping is a property of *your* project. It is many-to-many —
one survey of this suite maps 38 questions onto 45 checks across 48 pairs — and
several questions in such a survey are answered by things carrying no registry
id at all, or by comparing two artefacts, which no check over a single graph
can express.

Two accepted formats:

```json
{ "IRI construction pattern not updated following a model change": ["CNF-002", "TQL-001"] }
```

```csv
check_id,theme
CNF-002,IRI construction pattern not updated following a model change
TQL-001,IRI construction pattern not updated following a model change
```

A header row is tolerated without being declared: a check id has the shape
`ABC-123` and `check_id` does not.

Checks with findings that no question covers are listed under **not mapped to a
question** rather than omitted, so that "it found nothing" and "this survey
asks nothing about it" stay distinguishable — only the first means there is
nothing to do.
