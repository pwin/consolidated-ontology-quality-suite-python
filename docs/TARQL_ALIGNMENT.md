# TARQL/oxi-gen query <-> ontology alignment

> This doc covers one specific question in depth: do the queries and the
> ontology agree about prefixes, namespaces and declared terms?
> `docs/TESTING_TARQL.md` is the wider guide to testing a query folder --
> including the checks that need no ontology at all (BIND drift across files,
> unbound `CONSTRUCT` variables) and the order to run everything in.

`ontology_suite/sketch/prefix_alignment.py` checks whether a TARQL/oxi-gen
CONSTRUCT query file still matches the ontology it's meant to triplify
against. It exists for one recurring situation: an ontology changes (a
class/property gets renamed, a namespace IRI gets bumped to a new version)
and nothing forces every query file that builds data against it to be
updated in step -- the query still runs without error, still produces
clean-looking Turtle, it's just silently building data under vocabulary the
ontology no longer declares (or never declared in the first place). This
tool is the thing you run after an ontology change (or before trusting an
existing query against a new ontology version) to find out, concretely,
what in the query needs to change.

It checks two independent things:

1. **Declared prefixes/namespaces** -- does the query's `PREFIX` block
   still point at the same namespace IRIs the ontology uses?
2. **Class/property usage** -- does every class (`rdf:type`) and property
   the query's CONSTRUCT template actually builds exist in the ontology at
   all?

Both are static: they read the query and ontology files as text/RDF, they
don't run anything. For a stronger check against genuine triplified output,
see [Reviewing real output data](#reviewing-real-output-data) below.

## Commands

### Command line

```
python -m ontology_suite.sketch.prefix_alignment \
  --queries <query-file-or-folder> \
  --ontology <ontology-file>
```

- `--queries` -- a single `.rq`/`.sparql`/`.tarql`/`.tq` file, or a folder
  (repeatable -- pass it more than once for multiple sources).
- `--ontology` -- an ontology file (repeatable -- pass every file you want
  considered, e.g. the main ontology plus each import; there is no
  `owl:imports` resolution here).
- `--file-pattern` -- glob pattern(s) for folder mode
  (default `*.sparql,*.rq,*.tarql,*.tq`).
- `--ignore-prefix` -- add to the default-ignored structural prefixes
  (`owl`, `rdf`, `rdfs`, `xml`, `xsd`).
- `--fail-on-mismatch` -- exit `1` if anything is found (default: always
  exit `0`, report-only -- opt in to this for a CI gate).

Worked example, using this repo's own fixtures:

```
$ python -m ontology_suite.sketch.prefix_alignment \
    --queries examples/queries --ontology examples/ontology/domain.ttl

1 propert(y/ies) used in TARQL but not declared in the ontology set:
  [undeclared_property] https://example.org/demo/species
```

(That one is deliberate -- see the comment in `examples/queries/animals.rq`
-- included here so the example output isn't misleadingly empty.)

### Python API

For use in a script or test rather than the command line, call the checks
directly:

```python
from ontology_suite.sketch import prefix_alignment as pa

report = pa.check_tarql_ontology_alignment(
    ["path/to/queries"],            # files and/or folders
    ["path/to/ontology.ttl"],       # every ontology file to consider
)
if not report.is_clean:
    print(pa.format_alignment_report(report))
```

`report.prefix_misalignments` and `report.undeclared_terms` are the two
underlying lists if you want to inspect or filter findings programmatically
rather than just print them. The two checks are also callable individually:
`check_tarql_ontology_prefix_alignment(...)` and
`check_undeclared_terms(...)`.

### Reviewing real output data

Neither command above executes anything -- they read text/RDF. To check
what a transform *actually produces* (real literal values, real CSV data),
run it through `oxi-gen` and check the genuine output instead of the query
template. There's no CLI flag for this yet; it's a few lines of Python
against building blocks this suite already has:

```python
from ontology_suite import config
from ontology_suite.triplify import oxigen
from ontology_suite.triplify.discovery import TriplifyJob
from ontology_suite.dataquality import data_quality
import rdflib

binary = config.find_oxi_gen_binary()  # None if not built -- see below
oxigen.run_oxi_gen(binary, TriplifyJob(csv_path="data.csv", query_path="transform.rq"), "output.ttl")

output_graph = rdflib.Graph().parse("output.ttl", format="turtle")
declarations = data_quality.ontology_declarations(rdflib.Graph().parse("ontology.ttl"))
conformance = data_quality.check_conformance(declarations, output_graph)
print(conformance["undeclared_classes_used"], conformance["undeclared_properties_used"])
```

`tests/test_tarql_output_data_alignment.py` is a complete, runnable
worked example of this pattern (real CSV + real query + real ontology,
before/after an ontology version bump). Requires a built `oxi-gen` binary
(`cargo build --release` in the sibling checkout) -- `config.find_oxi_gen_binary()`
returns `None` if it isn't found, same as the `triplify`/`run` CLI stages.

## What the results mean

### Prefix/namespace findings (`report.prefix_misalignments`)

| kind | meaning | likely fix |
|---|---|---|
| `namespace_mismatch` | the query rebinds a prefix name the ontology set already uses, to a *different* IRI | almost always a real bug -- a stale IRI, a copy-pasted `PREFIX` line from a different ontology, or (most common after a version bump) the ontology's namespace moved and the query wasn't updated. Update the query's `PREFIX` line to the new IRI. |
| `prefix_name_mismatch` | same namespace IRI, different prefix label than the ontology set uses | harmless to run as-is -- purely a readability/consistency smell. Worth aligning the label so the query reads consistently with the ontology, but not urgent. |
| `undeclared_namespace` | neither the prefix name nor the IRI appears anywhere in the given ontology set | expected and fine for genuinely external vocabulary (common ones are pre-filtered via `ignore_prefixes`). Otherwise: check the right ontology file was actually passed to `--ontology` before assuming the query is wrong. |

### Undeclared-term findings (`report.undeclared_terms`)

Each entry is `kind` (`"class"` or `"property"`) and `term` (the full IRI).
This means the query's CONSTRUCT template uses that class (via `rdf:type`)
or property, but no file in the given `--ontology` set declares it at all.
Two explanations, and you have to look at the term to tell which:

- **The ontology needs a new declaration.** The vocabulary is intentional
  and simply hasn't been added to the ontology yet -- add the missing
  `owl:Class`/`owl:ObjectProperty`/`owl:DatatypeProperty` declaration.
- **The query needs fixing.** A typo, or vocabulary left over from before a
  rename -- update the query to the term the ontology actually declares.

`examples/queries/animals.rq`'s `ex:species` finding (shown above) is the
first kind, deliberately -- read the query file's own comment for why it's
left that way as a fixture.

### A clean result

Both checks return empty lists / `report.is_clean == True` when nothing
needs attention. `format_alignment_report`/the CLI print exactly that:

```
No prefix/namespace misalignments or undeclared classes/properties found.
```

### After an ontology version bump, specifically

Run the checks with the query as-is against the **new** ontology version.
Whatever comes back names exactly what changed and needs a query update --
that's the point of the tool, demonstrated concretely in
`tests/test_tarql_ontology_version_alignment.py` (a class/property rename
across two ontology versions) and `tests/test_tarql_output_data_alignment.py`
(the same scenario, but reviewing real triplified output instead of the
query text). A useful sanity check either way: run the same check with the
old ontology version first -- it should come back clean, confirming any
findings against the new version are genuinely about the version change and
not a pre-existing, unrelated problem in the query.

One thing to expect, not a bug: a namespace IRI bump on an otherwise
unchanged term produces *two* findings for the same root cause -- one
`namespace_mismatch` (prefix-level) and one `undeclared_class`/
`undeclared_property` (term-level, since the class/property IRI itself
changed along with the namespace). Both point at the same one-line
`PREFIX` fix; see `test_v1_transform_against_v2_ontology_is_a_namespace_mismatch`
in `tests/test_tarql_ontology_version_alignment.py` for the worked example.
