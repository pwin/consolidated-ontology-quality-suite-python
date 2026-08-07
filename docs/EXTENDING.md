# Extending the suite

There are three kinds of check, depending on what it needs to compute. Pick
the lightest one that fits.

## 1. A portable SPARQL `CONSTRUCT` check (most checks)

Fits anything expressible as a graph pattern over a single merged graph:
undefined terms, structural patterns, contradiction patterns, etc.

1. **Pick an id and category.** Ids follow `<CATEGORY-PREFIX>-<NNN>`, e.g.
   `STY-006`. Categories currently in use: `structural` (STR), `logical`
   (LOG), `quality` (QUA), `efficiency` (EFF), `style` (STY), `data` (DAT),
   `reasoning` (REA), `conformance` (CNF). Introduce a new category if the
   check doesn't fit any of these -- just add it to `registry.json` and to
   `CATEGORY_TITLES` in `docs/generate_checks_md.py`.

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
     or offending value
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

There is deliberately no dedicated "native check registry" separate from
`registry.json` -- one registry, three ways of producing a matching
`ResultRow`.

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
