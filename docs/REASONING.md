# Reasoning: OWL2 profiles and consistency checking

Two distinct questions, both handled by `ontology_suite/reasoning/`:

- **"How expressive is this ontology?"** -- `reasoning/profile.py`, a
  syntactic OWL2 EL/QL/RL profile-membership checker. No reasoner involved.
- **"Is this ontology (plus data) logically consistent?"** --
  `reasoning/consistency.py`, dispatching to one or two reasoning backends.

## OWL2 profile membership (`reasoning/profile.py`)

The OWL2 Profiles recommendation defines three tractable fragments of OWL2
DL, each trading expressiveness for a complexity/tooling guarantee:

| Profile | Guarantee | Typical engine |
|---|---|---|
| **EL** | Polynomial-time classification | ELK |
| **QL** | First-order-rewritable query answering (SQL-like) | Ontop and similar OBDA tools |
| **RL** | Rule-based reasoning | This suite's own `owlrl_backend.py`, or any OWL2 RL engine |

`check_profiles(graph, profiles=("EL","QL","RL"))` walks the asserted axioms
directly and flags the well-known constructs each requested profile
disallows -- `owl:unionOf`, `owl:complementOf`, universal (`allValuesFrom`)
restrictions, cardinality restrictions beyond 0/1, certain property
characteristics, and `owl:disjointWith` (for EL). This is a **heuristic,
syntactic** approximation, not a certified conformance checker:

- **Off by default.** The CLI defaults `profiles` to `()` -- no profile is
  checked, and no `REA-010`/`011`/`012` findings appear at all, unless you
  explicitly opt in with `--profile EL` / `--profile QL` / `--profile RL`
  (repeatable) on `ontology-quality-suite ontology` or `ontology-quality-suite run`. The
  ontology is assumed to be full OWL2 DL by default -- the common case,
  needing no profile-violation noise; ask for a specific profile only when
  you actually care whether the ontology fits it (e.g. before feeding it to
  ELK, ask `--profile EL`).
- It does not do full grammar-position analysis (e.g. QL's precise rules
  about which side of a subclass axiom a construct may appear on) beyond
  the few positions this suite specifically checks.
- It does not resolve `owl:imports` itself -- pass in the already-merged
  graph if imports should count (the `ontology` pipeline stage does this
  via `ontology_evaluation.resolve_imports` before calling it).
- **Exceeding a profile is not a defect.** `REA-010`/`011`/`012` are
  `Info`-severity by convention -- most ontologies are deliberately full
  OWL2 DL, and gist-style ontologies routinely use `gist:Category`
  enumerations (`owl:oneOf`) and similar constructs that are expected to sit
  outside EL/QL/RL. Treat these findings as an expressiveness *report*, not
  a to-do list, unless you specifically need one of these profiles' tooling.

If you need a certified answer (e.g. before feeding an ontology to ELK),
pair this suite with the real profile checker that ships with your DL
reasoner of choice -- this heuristic is meant to give a fast, dependency-free
first read, not replace one.

## Consistency checking (`reasoning/consistency.py`)

Two backends, always attempted in this order unless `--reasoner` says
otherwise:

### 1. `reasoning/backends/owlrl_backend.py` -- always on

Pure Python (the `owlrl` package, a hard dependency). Materializes an
RDFS/OWL2-RL deductive closure of the graph, then reruns
`sparql/logical/closure-safe/*.rq` and `sparql/reasoning/REA-00x.rq`
against the *closed* graph -- so a contradiction that only becomes visible
after inference (an individual's disjoint-class membership entailed via a
subclass chain, for instance) surfaces, not just directly-asserted ones.

**Sound but not complete** for full OWL2 DL: owlrl implements the OWL2 RL
rule set, a deliberately tractable fragment (see the profile table above).
A class can be genuinely unsatisfiable in full OWL2 DL without any RL rule
ever firing -- `REA-004` (an individual inferred into `owl:Nothing`) will
catch many real cases, but its absence is not a consistency proof.

**Not every `sparql/logical/*.rq` check is safe to rerun post-closure.**
Only `sparql/logical/closure-safe/` (`LOG-001`/`002`/`004`/`005`) is
rerun against the closure; plain `sparql/logical/` (`LOG-003`/`006`/`007`)
is checked pre-closure only, by the ordinary `checks` stage. Some "logical
cogency" checks describe a genuine contradiction that's just as real
whether asserted or entailed (a class disjoint with its own ancestor, a
functional property with two distinct values) -- rerunning those
post-closure is exactly the reasoning layer's purpose. Others describe
something about how the ontology's own axioms were *authored* -- a
redundant `equivalentClass`+`subClassOf` pair (`LOG-003`), a symmetric/
transitive property's own declared domain/range (`LOG-006`/`007`) -- and
rerunning *those* post-closure produces false positives, because OWL2 RL
unconditionally entails the reciprocal `subClassOf` from every
`equivalentClass` axiom (making `LOG-003` fire on every one, authored
redundantly or not) and RDFS's `rdfs:subPropertyOf` domain/range
propagation can entail a mismatched domain/range onto a property that
never declared either directly (`LOG-006`/`007`). Caught as a real bug
against a real vehicle ontology importing gist 14.1.0: `LOG-003` produced
164 findings post-closure -- every one of gist's 59 `equivalentClass`
axioms, none of them actually authored redundantly -- and `LOG-007` went
from 0 to 12. See `sparql/logical/closure-safe/README.md` for the full
per-check reasoning.

One subtlety worth knowing if you extend the reasoning-category checks:
**`owl:disjointWith` is not necessarily symmetrized by the closure.** If
`A owl:disjointWith B` is asserted, the closure may not add
`B owl:disjointWith A` -- a check that assumes symmetry (e.g. to dedupe
`(A,B)`/`(B,A)` pairs with a `FILTER(STR(?a) < STR(?b))`) needs to match
*either* direction explicitly (see `sparql/reasoning/REA-001.rq`'s `UNION`
for the fix, and `docs/EXTENDING.md`'s note on testing new checks -- this
exact bug shipped once already).

### 2. `reasoning/backends/external_backend.py` -- optional, best-effort

Uses `owlready2` (the `reasoner` extra: `uv sync --extra reasoner`) to run
a real OWL2 DL reasoner (HermiT by default, or Pellet) -- itself a Java
process owlready2 shells out to, so a working Java runtime is also
required. **Complete** for full OWL2 DL when it runs successfully: it can
prove a class unsatisfiable even when no RL rule fires.

Every entry point degrades gracefully rather than raising:

- `owlready2` not installed -> `REA-022` (Info): "only owlrl-based checks ran."
- `owlready2` installed but the reasoner invocation fails (no Java, an
  unsupported datatype, etc.) -> the same `REA-022`, with the specific
  failure in the message.
- Reasoner runs and finds an inconsistency -> `REA-020` (`Violation`).
- Reasoner runs and finds specific unsatisfiable named classes without the
  whole ontology being inconsistent -> `REA-021` per class (`Violation`).
- Reasoner runs and finds nothing wrong -> no rows at all.

This backend is exercised by real, JVM-invoking integration tests
(`tests/test_external_reasoner.py`, skipped automatically if Java/owlready2
aren't actually usable here) -- getting it to genuinely run, not just
gracefully report "unavailable", surfaced two real bugs worth knowing about
if you touch this file:

1. **Windows file URIs.** The ontology is serialized to a temp file and
   loaded into owlready2's `World`; loading it via `Path.as_uri()` (a
   `file:///C:/...` URI) fails on Windows because owlready2 strips the
   `file://` scheme naively, leaving an invalid `/C:/...` path. Fixed by
   passing the plain filesystem path string instead -- owlready2's
   `get_ontology()` accepts that directly.
2. **Inconsistency looks like an exception, not a result.** An inconsistent
   ontology makes `owlready2.sync_reasoner()` *raise*
   `owlready2.OwlReadyInconsistentOntologyError` rather than return
   normally with `Thing`/`Nothing` in `inconsistent_classes()`. A generic
   `except Exception` around the reasoner call will catch this and
   misreport it as "reasoner unavailable" (`REA-022`) instead of the
   correct `REA-020` -- it needs its own `except` clause ahead of the
   generic one.
3. **`xsd:date` isn't in the OWL2 datatype map** (only `xsd:dateTime` is --
   see the [OWL2 spec's Datatype Maps section](https://www.w3.org/TR/owl2-syntax/#Datatype_Maps)).
   HermiT correctly refuses to process an ontology using it, which is why
   `examples/ontology/domain.ttl` (which does use `xsd:date`, for the
   `DAT-001` invalid-lexical-form check) degrades to `REA-022` under the
   external backend rather than actually running -- expected, not a bug;
   `tests/test_external_reasoner.py` covers this exact case with its own
   fixture that avoids the datatype so the reasoner can actually complete.

`--reasoner auto` (the default across every CLI subcommand that takes
reasoning options) always runs owlrl and attempts the external backend;
`--reasoner owlrl-only` skips the external attempt entirely (useful in CI
environments where you don't want a `REA-022` info-noise finding just
because Java isn't installed there); `--reasoner hermit`/`--reasoner
pellet` picks which external reasoner to attempt; `--reasoner none` skips
reasoning entirely.

## Sampling large data graphs (`reasoning/sampling.py`)

The `data` pipeline stage's `--sample N` runs the (comparatively expensive)
reasoning pass over a Concise Bounded Description of `N` randomly chosen
named subjects -- every triple each sampled subject appears in as subject,
recursing one level into blank-node objects so multi-triple structures
(restrictions, RDF lists) aren't cut in half -- rather than the whole
graph. The registry's SPARQL/SHACL checks are unaffected by `--sample` and
always run over the complete graph, since they're comparatively cheap.
