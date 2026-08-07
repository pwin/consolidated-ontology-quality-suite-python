# consolidated-ontology-suite-python

Python tools for checking that an OWL2 ontology stays internally consistent
across versions, and that TARQL/oxi-gen CONSTRUCT-query transformation files
stay aligned with the ontology(ies) they triplify against -- namespaces,
class/property usage -- with **suggested diffs for automated repair**,
either against local files or a live Fuseki (SPARQL 1.1 Protocol)
triplestore.

This package carries forward the full `ontology_suite` pipeline from the
sibling `consolidated_ontology_suite` checkout (OntoQA/OQuaRE metrics,
SHACL+SPARQL registry checks, OWL2 profile/reasoner-backed consistency,
docgen -- the same logic is also ported to TypeScript in
`consolidated_ontology_suite_webapp`, the VS Code extension) and adds three
things that didn't exist in Python before:

- **Ontology-version consistency + rename detection**
  (`ontology_suite.versioning.rename_detection`) -- pairs up a version
  diff's removed/added classes and properties into probable renames, using
  either an explicit `owl:equivalentClass`/`dcterms:isReplacedBy`
  migration annotation (high confidence) or local-name similarity
  (fallback).
- **Automated repair suggestions** (`ontology_suite.repair`,
  `ontology_suite.checks.repair`) -- turns a TARQL/ontology misalignment or
  a registry-check finding into a concrete unified diff: a `PREFIX` line
  fix, an IRI-rename substitution across a query file, an ontology
  declaration stub, or a real SPARQL 1.1 Update -- reviewable as a
  `.patch` file, or applied directly.
- **Live triplestore support** (`ontology_suite.remote`) -- runs the same
  checks against a Fuseki (or any SPARQL 1.1 Protocol) dataset, named-graph
  aware: diff two ontology *versions* held as separate named graphs, run
  the existing registry `.rq` checks scoped to exactly the graphs they
  should see, or three-way check a live triplified named graph against its
  ontology graph *and* its local TARQL source file.

See `docs/CONSISTENCY_AND_REPAIR.md` for the local-file workflow,
`docs/FUSEKI.md` for the live-triplestore one, `docs/ARCHITECTURE.md` for
the inherited pipeline this all sits on top of, and
`docs/UPSTREAM_README.md` for that pipeline's own original README.

## Quick start

```bash
uv sync
uv run ontology-suite consistency --new domain-v2.ttl --old domain-v1.ttl --queries queries/
```

```python
from ontology_suite import consistency

report = consistency.check_consistency(
    "domain-v2.ttl", old_ontology="domain-v1.ttl", tarql_sources=["queries/"],
)
print(consistency.format_consistency_report(report))
consistency.apply_repairs(report.repairs, min_confidence=0.7)
```

## CLI

```
ontology-suite ontology           --ontology domain.ttl
ontology-suite checks             --ontology domain.ttl [--data data.ttl]
ontology-suite sketch             --queries queries/ [--ontology domain.ttl]
ontology-suite triplify           --csv-dir csv/ --queries queries/
ontology-suite data                data.ttl [more.ttl ...] [--ontology domain.ttl]
ontology-suite docgen             --ontology domain.ttl
ontology-suite run                whichever of --ontology/--queries/--csv-dir/--data apply
ontology-suite version-diff        old.ttl new.ttl
ontology-suite consistency        --new domain.ttl [--old domain-v1.ttl] [--queries queries/] [--apply-repairs]
ontology-suite consistency-remote --query-endpoint URL --manifest graphs.json
```

## Tests

```bash
uv run pytest
```
