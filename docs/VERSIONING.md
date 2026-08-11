# Ontology version diffing

`ontology_suite/versioning/diff.py` compares two versions of an ontology and
suggests a semantic-versioning-style bump (MAJOR/MINOR/PATCH/NONE):

```
ontology-quality-suite version-diff old.ttl new.ttl
ontology-quality-suite version-diff old.ttl new.ttl --json --out-dir out/version-diff
```

`owl:imports` are resolved transitively by default (same machinery as the
`ontology` stage, `ontology_evaluation.py::resolve_imports`) -- pass
`--exclude-imports` to diff each file alone, or `--import-dir`/
`--allow-network` the same way `ontology-quality-suite ontology` accepts them.

## The rules

This is a **heuristic**, not a formal compatibility proof -- same spirit as
`reasoning/profile.py`. It compares named classes/properties,
`rdfs:subClassOf` edges, `rdfs:domain`/`rdfs:range`, `owl:disjointWith`,
`owl:equivalentClass`/`equivalentProperty`, and the seven OWL2 property
characteristics.

**MAJOR** (breaking -- something depending on the old ontology could break):
a class or property was removed; a subclass edge was removed (both classes
still exist); a new disjointness axiom was added; a property's domain or
range was narrowed (or changed incomparably); a property gained a new
*restricting* characteristic (`Functional`/`InverseFunctional`/
`Asymmetric`/`Irreflexive`); an `equivalentClass`/`equivalentProperty` axiom
was removed.

**MINOR** (additive, backward-compatible -- checked only if nothing above
matched): new classes/properties; new subclass edges; widened domain/range;
a *relaxed* (removed) characteristic; a newly added equivalence axiom; a
removed (relaxed) disjointness axiom.

**PATCH**: no structural change, but at least one term's `rdfs:label` or
`rdfs:comment` text differs.

**NONE**: no difference in anything this module tracks.

## Domain/range narrowing is hierarchy-aware

Moving a property's domain from a subclass to its superclass (e.g.
`ex:Dog` -> `ex:Animal`) is a **widening**, not an unrelated/incomparable
change -- anything typed `ex:Dog` was already inferred `ex:Animal` via
`rdfs:subClassOf`, so the new, looser constraint still accepts everything
the old one did. `diff.py` expands each domain/range class set through that
version's own subclass hierarchy before comparing (`_expand_with_subclasses`)
rather than comparing the bare class IRIs. An empty domain/range (no
constraint declared at all) is treated as "unconstrained", not as the
empty set it literally is -- so *adding* a domain/range where none existed
is a narrowing (MAJOR), and *removing* one entirely is a widening (MINOR).

## Blank nodes are out of scope

Anonymous class expressions (`owl:Restriction`, `unionOf`, `intersectionOf`,
...) are **not** diffed by identity. A blank node's label is never stable
across two independent parses of "the same" expression -- rdflib mints a
fresh one from an internal counter every time -- so comparing them across
old/new snapshots would make every anonymous expression look
removed-and-re-added even when byte-for-byte identical in meaning. This
surfaced as a real bug while validating this tool against gist's own
version history (see below): every transition misclassified as MAJOR until
blank nodes were excluded from the class/domain/range comparisons. This
suite already scopes anonymous class expressions out of the named class
hierarchy elsewhere (`ontology_evaluation.py`'s DIT/NOC/NAC metrics); the
version-diff tool follows the same convention rather than attempting
blank-node-aware graph isomorphism matching, which is a substantially harder
problem this tool doesn't attempt to solve.

## Validated against real gist releases

`examples/gist_versions_reference/` contains seven real, consecutively
released versions of Semantic Arts' gist ontology (`gistCore10.0.0.ttl`
through `gistCore14.1.0.ttl`). gist's own release history already
classifies each consecutive pair as major or minor (an `X.0.0` release is
major, an `X.Y.0` release with `Y>0` is minor); `tests/test_versioning_diff_gist.py`
runs this tool against every consecutive pair and asserts the heuristic's
classification matches gist's own published bump level -- a real-world
check, not just engineered synthetic examples, and the reason the
blank-node bug above was caught at all (small hand-written test ontologies
never happened to exercise it).

| Transition | gist's own bump | This tool's classification |
|---|---|---|
| 10.0.0 -> 11.0.0 | major | major |
| 11.0.0 -> 12.0.0 | major | major |
| 12.0.0 -> 12.1.0 | minor | minor |
| 12.1.0 -> 13.0.0 | major | major |
| 13.0.0 -> 14.0.0 | major | major |
| 14.0.0 -> 14.1.0 | minor | minor |

Six real transitions is not a proof the heuristic always agrees with human
judgment -- gist's maintainers may classify a change as "minor" that this
tool's stricter structural rules would flag as breaking, or vice versa.
Treat a MAJOR/MINOR/PATCH result as a strong, evidence-based *suggestion*
to sanity-check against the actual changelog, not an authoritative verdict.
