# Extending the suite

There are three kinds of check, depending on what it needs to compute. Pick
the lightest one that fits.

> `docs/check-registry.html` covers the same ground as a self-contained
> page, with the full catalogue of registered checks alongside it -- useful
> for sharing with people who are choosing or reviewing checks rather than
> writing them. Regenerate it with `python docs/generate_check_registry.py`
> after any change here.

**Paths below are relative to `ontology_suite/resources/`** (i.e.
`registry.json` below means `ontology_suite/resources/registry.json`,
`sparql/<category>/<id>.rq` means
`ontology_suite/resources/sparql/<category>/<id>.rq`) -- both in this
repo's own source tree and, once installed, inside the package itself
(`<site-packages>/ontology_suite/resources/`, findable via
`python -c "from ontology_suite import config; print(config.PACKAGE_RESOURCES)"`).
Editing a pip-installed copy directly works but doesn't survive an
upgrade; `checks`/`data`/`run`'s `--registry`/`--shapes`/`--sparql` flags
let you point at your own copy instead without touching the installed
package at all -- see `docs/PRIMER.md` §13 for the full copy-edit-point-at
worked example, verified against a real `pip install`.

## 1. A portable SPARQL `CONSTRUCT` check (most checks)

Fits anything expressible as a graph pattern over a single merged graph:
undefined terms, structural patterns, contradiction patterns, etc.

1. **Pick an id and category.** Ids follow `<CATEGORY-PREFIX>-<NNN>`, e.g.
   `STY-006`. Categories currently in use: `structural` (STR), `logical`
   (LOG), `quality` (QUA), `efficiency` (EFF), `style` (STY), `data` (DAT),
   `reasoning` (REA), `conformance` (CNF), `tarql` (TQL). Introduce a new
   category if the check doesn't fit any of these -- add it to
   `registry.json`, to `CATEGORY_TITLES` in `docs/generate_checks_md.py`,
   and to `CATEGORIES` in `docs/generate_check_registry.py` (both doc
   generators carry their own map, and the second one fails its test if a
   category is missing rather than silently dropping the checks in it).

2. **Add an entry to `registry.json`:**

   ```json
   {
     "id": "STY-006",
     "category": "style",
     "metric": "short description of what is being measured",
     "default_severity": "Warning",
     "title": "One-line summary",
     "description": "Full prose description of the condition being flagged.",
     "remediation": "What the user should do to fix it.",
     "cucumber_feature": "Naming Style",
     "cucumber_scenario": "One sentence, phrased as an expectation that should hold"
   }
   ```

3. **Write `sparql/<category>/<id>.rq`.** It must:
   - `CONSTRUCT` a blank node typed `sh:ValidationResult`
   - always set `sh:resultSeverity`, `sh:focusNode`, `sh:resultMessage`, and
     `sh:sourceConstraintComponent oq:<id>`
   - set `sh:resultPath` and/or `sh:value` when there's a natural predicate
     or offending value. Binding *several* of either on one result is fine
     and is sometimes the honest thing to do (`LOG-004` names both of the
     inverses it is complaining about, `LOG-006`/`LOG-007` both the domain
     and the range) -- `checks/merge.py` sorts and joins them, so the
     finding renders and dedups identically however the engine happens to
     order them
   - be self-contained (its own `PREFIX` declarations, no external state)

   Copy an existing query in the same category as a starting point. Before
   trusting a new query, run it directly and check the result count -- see
   "A note on testing new checks" below; a filter or `UNION` that looks
   obviously correct can still silently match nothing (this suite's own
   `REA-001` shipped with exactly this bug: a `FILTER(STR(?c1) < STR(?c2))`
   that assumed `owl:disjointWith` gets symmetrized by reasoning, which it
   doesn't).

4. **(Recommended) add a SHACL shape too**, in `shapes/<category>.ttl`.
   Prefer native SHACL core constraints (`sh:minCount`, `sh:pattern`,
   `sh:or`, `sh:disjoint`, property paths) when the check maps cleanly onto
   them; otherwise `sh:sparql` with a `sh:select` mirroring the `.rq`
   file's `WHERE` clause. If the shape's own IRI is exactly `oq:<id>`, no
   further annotation is needed; if a check needs several NodeShapes, add
   `oq:checkId "<id>"` to each shape node (see `ontology_suite/checks/registry.py::resolve_check_id`).

   **Declare `sh:severity` on the shape node, matching the check's
   `default_severity`** -- never inside the `sh:sparql [ ... ]` block. SHACL
   defines `sh:severity` as a property of a shape; putting it on the nested
   `sh:SPARQLConstraint` parses fine and is then read by some processors and
   ignored by others (pyshacl silently substitutes `sh:Violation`), so the
   same shape yields different severities under different `--engine` values.
   `tests/test_shape_severity.py` fails on both mistakes -- wrong placement,
   and a severity that disagrees with `registry.json`.

5. **Regenerate `docs/CHECKS.md`:** `python docs/generate_checks_md.py`.

6. **Nothing else to touch.** `ontology_suite/checks/sparql_runner.py`
   walks `sparql/**/*.rq` and `shacl_runner.py` loads every `shapes/*.ttl`
   file generically; `registry.py` resolves ids on the fly. The new check
   automatically appears in every table, plot, and Cucumber feature/scenario.

## 2. Rerunning existing checks against a transformed graph

If a check should look at an *entailed* graph rather than the raw one (like
`REA-001`..`REA-004` do against the owlrl closure), write it exactly like
any other portable SPARQL check (step 1) but put it under
`sparql/reasoning/`, and make sure whatever calls
`ontology_suite/reasoning/backends/owlrl_backend.py::run_owlrl_checks`
includes that directory in its `sparql_dirs` list (it already includes
`sparql/logical` and `sparql/reasoning` by default). No SPARQL/registry
changes needed beyond the check itself.

## 2b. A check over query *source* (`sparql/tarql/`)

Fits anything about the TARQL/oxi-gen queries themselves rather than the
graph they build: an IRI template that drifts between files, a `BIND` that
does not follow a house convention, a `CONSTRUCT` variable that is not what
it looks like.

This used to be the standing example of something only Python could do, and
the reasoning was sound but the conclusion too broad. Query source is not a
graph -- `sketch.ttl` keeps each query's `CONSTRUCT` template and discards
the `WHERE` clause, so every `BIND` is gone before it exists. That is a
reason `TQL-001`..`TQL-003` are Python. It is not a reason a TARQL check
*cannot* be a query, because the run now publishes the query source as its
own graph: `bind-facts.ttl`, one node per `BIND` carrying its target,
expression, skeleton, file and line, plus one per `CONSTRUCT` variable
(`sketch/bind_analysis.py::bind_report_to_graph`).

So the recipe is §1's, with two differences:

- The `.rq` goes in **`sparql/tarql/`**, and is written against the `tq:`
  vocabulary rather than against RDFS/OWL.
- That directory is **held back from the ontology and data sweeps**
  (`checks/sparql_runner.py::SUBJECT_SPECIFIC_DIRS`). A check that runs
  against every graph and matches almost everywhere looks exactly like one
  that has quietly stopped working, so these are run only against the graph
  they are about.

The line is *not* difficulty. `TQL-001`'s cross-file comparison is a nested
aggregate with two `COUNT DISTINCT`s, and it is perfectly expressible as a
query now that the skeleton it compares is a published fact -- it stays
native because it predates the facts graph, not because SPARQL cannot do it.
What SPARQL cannot do is *compute* the skeleton, which is a parse. The rule
that follows: if a `FILTER` is recovering structure from the expression text,
add a predicate to `bind_report_to_graph` instead.

`sparql/tarql/TQL-004.rq` is the worked example of exactly that -- its first
draft searched the text for `"IRI("`, which misreads `MYIRI(` and misses a
bare string literal; it now reads a single `tq:producesIri` triple. **And
`docs/TESTING_TARQL.md` is the full write-up** -- the vocabulary, the
manifest fields, the rdflib SPARQL-parser traps, and how to run a check tree
that lives in your own project rather than in this package.

## 3. A native Python check (when there's no single graph to pattern-match)

Some things aren't a SPARQL pattern over one merged graph: OWL2 profile
membership (`reasoning/profile.py`), a real DL reasoner's output
(`reasoning/backends/external_backend.py`), comparing a graph against a
*separate* ontology's declarations (`dataquality/data_quality.py`'s
`check_conformance`), or comparing two ontology *versions*
(`versioning/diff.py`). For these:

1. Still add a `registry.json` entry (same fields as above) -- this is what
   lets the finding flow through the same report layer as everything else.
2. Write a function that returns `ontology_suite.checks.merge.ResultRow`
   instances directly, constructed with `check_id="<your-id>"`,
   `category="<your-category>"`, and whatever `focus_node`/`path`/`value`/
   `message`/`remediation`/`sources` make sense. Look at
   `reasoning/profile.py::profile_report_to_rows` or
   `dataquality/data_quality.py::conformance_to_rows` for the pattern.
3. Wire the function into the relevant `pipeline.py` stage so its rows get
   included in `run_*_stage`'s returned `StageResult.rows`.
4. Regenerate `docs/CHECKS.md`.

**If it walks a hierarchy or a blank-node structure, walk it iteratively.**
Depth is set by the input -- the longest `rdfs:subClassOf` chain, or the
length of an RDF collection, which is a `rdf:rest` chain one cell per member
-- and CPython's recursion limit is 1000 frames. Reuse
`ontology_suite/hierarchy.py` rather than writing a fifth copy of the same
walk; if you need a different shape, copy its explicit-stack pattern. Do not
reach for `sys.setrecursionlimit` (it doesn't grow the C stack, so it trades
a catchable error for a hard crash) or a depth cap (any value safe under a
1000-frame ceiling also truncates real answers). `tests/test_hierarchy.py`
guards both.

There is deliberately no dedicated "native check registry" separate from
`registry.json` -- one registry, three ways of producing a matching
`ResultRow`.

`sketch/bind_analysis.py` is the clearest example of when this third kind is
the *only* option rather than merely the easiest. It reviews the `BIND`
statements across a folder of TARQL queries -- looking for one variable
minted by structurally different expressions in different files, and for
constructed-IRI variables used in a `CONSTRUCT` template but never bound.
None of that is reachable from a graph pattern: `tarql_visualiser.parse_query`
keeps only each query's prefixes and its `CONSTRUCT` template, so the `WHERE`
clause and every `BIND` expression in it are gone before the sketch graph
exists. A check of this kind reads the source text and returns `ResultRow`s
directly.

## A note on testing new checks

Every existing check in this suite was hand-reviewed once and executed for
the first time only when this consolidated suite was actually run
end-to-end -- and that process caught real bugs (a namespace-legend filter
that silently matched nothing due to a `urllib.parse.urljoin` quirk; the
`REA-001` symmetry bug mentioned above; a blank-node identity bug in the
version-diff tool that made every anonymous class expression look
removed-and-re-added). **Actually run a new check against a small graph
that should and shouldn't trigger it** before trusting it -- e.g.:

```python
from rdflib import Graph
g = Graph(); g.parse("some-test-file.ttl", format="turtle")
q = open("sparql/<category>/<id>.rq", encoding="utf-8").read()
result = g.query(q)
print(len(list(result.graph)))
```

A query that "looks right" but matches zero triples against a fixture that
should trigger it is a bug, not a clean bill of health.

## A real worked example

`examples/acme_robotics/custom_checks/` is a real, minimal project-local
registry (one check, `ACM-001`) built following exactly the steps above --
see `docs/ACME_ROBOTICS_WALKTHROUGH.md` §4 for the full walkthrough,
including verifying it against real triplified data with zero false
positives and a deliberately incomplete record that correctly fires it.
