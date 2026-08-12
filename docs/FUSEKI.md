# Running against a live Fuseki (or any SPARQL 1.1 Protocol) triplestore

`ontology_suite.remote` runs this suite's checks against a live triplestore
instead of local files -- no dependency beyond the Python standard library
(`urllib`) and `rdflib`, and no Fuseki-specific API: everything here is
plain SPARQL 1.1 Protocol, so it works against any conformant store.

## Why this needs its own design, not just "point the existing checks at a URL"

A Fuseki **dataset is a union of named graphs**, not one graph. This
suite's three domains map onto that independently, and conflating them is
exactly the kind of mistake this module exists to prevent:

- The **ontology** typically lives in one named graph per version (e.g.
  `<https://.../graph/ontology/2.0.0>`).
- Each **TARQL/oxi-gen transform**'s real output usually gets loaded into
  its own named graph, one per triplify job.
- The **TARQL query file itself is never in the triplestore at all** -- it's
  a local artifact on disk. So "does the transform's *template* still match
  the ontology" and "does what the transform actually *produced* match the
  ontology" are two genuinely different checks here, not one.

Querying the whole dataset unscoped would silently mix ontology triples,
old-version triples, and every triplify job's output together -- the
worst-case result being a check that "passes" only because a stray older
graph happens to declare the class a newer one is missing.
`ontology_suite.remote.fuseki` scopes every query explicitly instead.

## Two ways data comes out of the store

**Materialize** (`fuseki.load_named_graph`) -- one `CONSTRUCT` pulls a named
graph down whole into a local `rdflib.Graph`, which every other function in
this suite (`versioning.diff.snapshot`,
`dataquality.data_quality.check_conformance`, ...) already knows how to use
unchanged. This is how two ontology versions get diffed live:

```python
from ontology_suite.remote import fuseki
from ontology_suite.versioning import diff as version_diff

dataset = fuseki.FusekiDataset(query_endpoint="http://localhost:3030/myds/sparql")
old_graph = fuseki.load_named_graph(dataset, "https://example.org/graph/ontology/1.0.0")
new_graph = fuseki.load_named_graph(dataset, "https://example.org/graph/ontology/2.0.0")
diff, bump = version_diff.diff_ontologies(old_graph, new_graph)
```

Pass `limit=` to cap how many triples are pulled for a graph too large to
pull whole.

**Scope a query to specific graphs, without materializing anything**
(`fuseki.run_registry_checks_remote`) -- the registry's own `.rq` CONSTRUCT
checks (`checks/sparql_runner.py`) are already whole, self-contained
queries. Each one is POSTed straight to the endpoint with the SPARQL 1.1
Protocol's repeatable `default-graph-uri` parameter set to exactly the
named graph(s) it should see -- e.g. one ontology graph plus one data
graph, mirroring how `pipeline.run_checks_stage` merges the two into one
local `working_graph` before running the same checks locally. The *existing*,
unmodified check queries then run correctly scoped, with no download step
and no query rewriting:

```python
results_graph, outcomes = fuseki.run_registry_checks_remote(
    dataset, "sparql/",
    default_graph_uris=[
        "https://example.org/graph/ontology/2.0.0",
        "https://example.org/graph/triplified/animals",
    ],
)
```

This works because the SPARQL 1.1 Protocol's `default-graph-uri` restricts
what an *unprefixed* `{ ?s ?p ?o }` pattern in the query sees to exactly the
graphs listed -- no `GRAPH <...> { }` clause needs adding to the check
files themselves.

## Named-graph provenance: `remote.manifest`

Fuseki has no built-in link from a named graph back to the local TARQL/
oxi-gen query file that produced it, or from a data graph to the ontology
graph it's meant to conform to -- a triplestore holds triples, not that
kind of metadata. `remote.manifest.GraphManifest` is where a project
records the binding explicitly:

```json
{
  "graphs": [
    { "graph_uri": "https://example.org/graph/ontology/2.0.0", "role": "ontology" },
    {
      "graph_uri": "https://example.org/graph/triplified/animals",
      "role": "triplified_data",
      "source_tarql": "queries/animals.rq",
      "ontology_graph_uri": "https://example.org/graph/ontology/2.0.0",
      "notes": "rebuilt weekly via cron"
    }
  ]
}
```

Each entry (`GraphBinding`) is a flat object:

| Field | Required | Applies to | Meaning |
|---|---|---|---|
| `graph_uri` | yes | both roles | the named graph's URI in the store |
| `role` | yes | both roles | exactly `"ontology"` or `"triplified_data"` -- no other values |
| `source_tarql` | no | `triplified_data` only | local path to the query file that produced this graph |
| `ontology_graph_uri` | no | `triplified_data` only | which `"ontology"`-role graph this data graph should conform to |
| `notes` | no | either | free text for humans -- never read by any check |

`GraphManifest.save()` always writes every field of every binding (`null`
for anything unset), so a saved file always shows this full shape, not
just what you filled in. `source_tarql`/`ontology_graph_uri` are
meaningless on an `"ontology"`-role binding -- they describe what a *data*
graph points back to, not the reverse.

Python API, as an alternative to hand-writing the JSON:

```python
from ontology_suite.remote.manifest import GraphManifest, GraphBinding

manifest = GraphManifest(bindings=[
    GraphBinding(graph_uri="https://example.org/graph/ontology/2.0.0", role="ontology"),
    GraphBinding(
        graph_uri="https://example.org/graph/triplified/animals", role="triplified_data",
        source_tarql="queries/animals.rq", ontology_graph_uri="https://example.org/graph/ontology/2.0.0",
    ),
])
manifest.save("graphs.json")

loaded = GraphManifest.load("graphs.json")
loaded.ontology_bindings()   # every role="ontology" entry
loaded.data_bindings()       # every role="triplified_data" entry
```

```
ontology-quality-suite consistency-remote \
  --query-endpoint http://localhost:3030/myds/sparql \
  --manifest graphs.json \
  --sample-limit 5000
```

`--sample-limit` caps how many triples get pulled per named graph when
materializing it (`fuseki.load_named_graph`'s `limit` parameter) -- see
that function's own docstring for why this isn't a true CBD sample, just
a straightforward cap; omit it for graphs known to be small (the common
case: one triplify job's output).

**`source_tarql`/`ontology_graph_uri` are each independently optional**,
and `check_named_graph_consistency` degrades gracefully rather than
erroring when one is missing -- it just skips whichever checks need it,
with a warning in the report rather than a failure:

| `ontology_graph_uri` set? | `source_tarql` set? | Checks that run |
|---|---|---|
| yes | yes | all three |
| yes | no | live data vs. ontology only |
| no | yes | template vs. live data only |
| no | no | none -- not a useful binding in practice |

For each `"triplified_data"` binding, `check_named_graph_consistency` runs
**three** independent checks, each catching a different failure mode:

1. **Template vs. ontology** (static) -- does `source_tarql`, as text,
   reference only classes/properties the ontology graph actually declares?
   Uses `sketch.prefix_alignment.check_undeclared_terms` (IRI-identity
   based) against the ontology graph materialized from the store.

   **Note:** the *prefix*-label half of `sketch.prefix_alignment`
   (`check_tarql_ontology_prefix_alignment`) is deliberately **not** run
   here. A SPARQL store holds triples, not `@prefix` declarations -- a
   named graph materialized via `CONSTRUCT` has no ontology-author-chosen
   prefix labels of its own to compare the query's `PREFIX` lines against;
   that information doesn't survive the round trip even though every
   triple does. Prefix-alignment checking is exact and meaningful for the
   *local-file* case (`consistency.check_consistency`), where a real `.ttl`
   file with real `@prefix` lines is available.

2. **Live data vs. ontology** -- does what's *actually* sitting in the
   named graph right now conform to the ontology's declarations? Catches
   real-world drift the static template can't: a CSV value that
   triplifies into something the template's own shape doesn't reveal, or a
   graph that was never re-triplified after the query *was* fixed.

3. **Template vs. live data** -- do the classes/properties the query
   template *says* it builds match what's actually in the named graph
   right now? A mismatch here, independent of what the ontology says,
   points at an oxi-gen/triplify execution problem (wrong file loaded, a
   stale run, an upstream CSV schema change) rather than a modelling
   problem.

## Applying a repair remotely

`checks.repair.compute_repair`'s output (`RepairOutcome.update_text`) is a
real SPARQL 1.1 Update -- the same text can be applied to a local in-memory
graph (via `Graph.update()`, what `compute_repair` already does to compute
the outcome) or posted straight to a live dataset:

```python
from ontology_suite.remote import fuseki

fuseki.apply_repair_remote(dataset, outcome.update_text)
```

`apply_repair_remote`/`fuseki.run_update` accept `using_graph_uris` (the
Protocol's `using-graph-uri` parameter) to scope the update's `WHERE`
match to specific graphs -- the update-side equivalent of
`default-graph-uri`. The update text's own `INSERT`/`DELETE` templates
still need an explicit `GRAPH <...> { }` or `WITH <...>` clause to target
a specific graph if the dataset has more than one; `using-graph-uri` alone
only scopes *matching*, not *writing*.
