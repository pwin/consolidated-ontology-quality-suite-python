# Why these four checks live in their own subdirectory

`LOG-001.rq`, `LOG-002.rq`, `LOG-004.rq`, and `LOG-005.rq` live here instead
of directly in `sparql/logical/` because `reasoning/consistency.py` reruns
*only this subdirectory* (plus `sparql/reasoning/`) against the owlrl
deductive closure -- everything in plain `sparql/logical/` (`LOG-003`,
`LOG-006`, `LOG-007`) is checked pre-closure only, by the ordinary `checks`
stage.

Both `sparql/logical/` and `sparql/logical/closure-safe/` are still
recursively discovered as normal by the `checks` stage's registry-driven
suite (`sparql_runner.discover_queries` walks the whole `sparql/` tree) --
this split only changes what the *reasoning* layer reruns against the
closure, not what the ordinary check suite runs at all.

The split exists because not every "logical cogency" check describes a
genuine contradiction that becomes *more* detectable after inference --
some describe something about how the ontology's own axioms were
*authored*, and rerunning those against an already-closed graph produces
false positives:

- **`LOG-003`** (redundant `equivalentClass` + `subClassOf` pair) is
  vacuously true for *every* `owl:equivalentClass` axiom once the closure
  is materialized -- OWL2 RL entails the reciprocal `subClassOf` from
  `equivalentClass` unconditionally, so a check for "was this redundancy
  actually authored" becomes meaningless once that entailment has already
  happened. Confirmed against a real vehicle ontology importing gist
  14.1.0: 59 of 59 `equivalentClass` axioms produced this false positive,
  none of them authored redundantly.
- **`LOG-006`**/**`LOG-007`** (symmetric/transitive property with unequal
  domain/range) check the property's *own directly declared* domain/range
  -- but RDFS entails domain/range onto a property via
  `rdfs:subPropertyOf` (if `P rdfs:subPropertyOf Q` and `Q` has a
  domain/range, `P` inherits it), so a property with no domain/range of
  its own can pick up a mismatched pair purely from its superproperty
  post-closure. Confirmed for `LOG-007` on the same real ontology (0 -> 12
  findings); `LOG-006` shares the identical vulnerability structurally
  even though this particular ontology didn't happen to trigger it.

`LOG-001` (class disjoint with its own transitive ancestor), `LOG-002`
(functional property violated by two distinct values), `LOG-004`
(property with more than one distinct declared inverse), and `LOG-005`
(property declared inverse of itself) all describe something that is -- or
would be -- a genuine logical problem regardless of whether the graph
triples that produce it were directly asserted or only entailed, so
rerunning them against the closure is exactly the point of the reasoning
layer: catching a contradiction that's only visible after inference.
