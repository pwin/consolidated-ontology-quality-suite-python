"""Runs this suite's checks against a live Fuseki (or any SPARQL 1.1
Protocol-compliant) triplestore, instead of local files.

A Fuseki *dataset* is not one graph -- it's a union of named graphs -- and
this suite's three domains map onto that independently:

- the **ontology** typically lives in one named graph per version (e.g.
  ``<https://.../graph/ontology/2.0.0>``), so two versions can be diffed
  live via two `load_named_graph` calls feeding
  ``versioning.diff.diff_ontologies``, without ever downloading a file;
- each **TARQL/oxi-gen transform**'s real output usually gets loaded into
  its own named graph, one per triplify job -- the natural live-store
  analogue of ``triplify.discovery``'s local CSV/tarql stem-pairing;
- the **TARQL query file itself is never in the triplestore at all** -- it's
  a local artifact -- so "does the transform still match the ontology"
  (static, text-level -- `sketch.prefix_alignment`, run against a graph
  materialized *from* the store with `load_named_graph`) and "does what the
  transform actually *produced* match the ontology" (live -- query the data
  named graph directly) are two genuinely different checks here, not one.
  ``remote.manifest`` ties the two together for a given named graph.

Two ways data comes out of the store:

- **Materialize** (`load_named_graph`) -- one ``CONSTRUCT`` pulls a named
  graph down whole into a local `rdflib.Graph`, which every other function
  in this suite (`versioning.diff.snapshot`,
  `dataquality.data_quality.check_conformance`, ...) already knows how to
  use unchanged. Pass `limit` to bound a graph too large to pull whole (a
  coarser version of `reasoning.sampling.sample_graph`'s CBD sampling,
  which needs the graph materialized locally first -- see `remote.manifest`
  for how the two compose).
- **Scope a query to specific graphs, without materializing anything**
  (`run_registry_checks_remote`) -- the registry's own ``.rq`` CONSTRUCT
  checks (`checks/sparql_runner.py`) are already whole, self-contained
  queries; each one is POSTed straight to the endpoint with the SPARQL 1.1
  Protocol's ``default-graph-uri`` parameter(s) set to exactly the named
  graph(s) it should see (e.g. one ontology graph + one data graph,
  mirroring how `pipeline.run_checks_stage` merges the two into one local
  `working_graph` before running the same checks locally) -- so the
  *existing*, unmodified check queries run correctly scoped, without a
  download step.
"""
from __future__ import annotations

import base64
import io
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, cast

from rdflib import Graph
from rdflib.query import Result, ResultRow

from ..checks.sparql_runner import SparqlCheckOutcome, discover_queries

_QUERY_FORM_PATTERN = re.compile(
    r"^\s*(?:(?:PREFIX|BASE)\s+\S+\s*(?:<[^>]*>)?\s*|#[^\n]*\n)*\s*(CONSTRUCT|SELECT|ASK|DESCRIBE)\b",
    re.IGNORECASE,
)

_ACCEPT_FOR_FORM = {
    "SELECT": "application/sparql-results+json",
    "ASK": "application/sparql-results+json",
    "CONSTRUCT": "text/turtle",
    "DESCRIBE": "text/turtle",
}


class FusekiError(RuntimeError):
    pass


@dataclass
class FusekiDataset:
    """Connection details for one Fuseki (or SPARQL 1.1 Protocol) dataset.
    ``query_endpoint``/``update_endpoint`` are the dataset's own `/sparql`
    (or `/query`) and `/update` service URLs, e.g.
    ``http://localhost:3030/myds/sparql`` and
    ``http://localhost:3030/myds/update``.
    """
    query_endpoint: str
    update_endpoint: Optional[str] = None
    auth: Optional[Tuple[str, str]] = None
    timeout: float = 60.0


def detect_query_form(query_text: str) -> str:
    match = _QUERY_FORM_PATTERN.match(query_text)
    if not match:
        raise FusekiError(f"Could not detect SPARQL query form (CONSTRUCT/SELECT/ASK/DESCRIBE) in: {query_text[:120]!r}")
    return match.group(1).upper()


def _headers(dataset: FusekiDataset, accept: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": accept}
    if dataset.auth:
        token = base64.b64encode(f"{dataset.auth[0]}:{dataset.auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return headers


def run_query(
    dataset: FusekiDataset,
    query_text: str,
    default_graph_uris: Sequence[str] = (),
    named_graph_uris: Sequence[str] = (),
) -> Result:
    """POSTs `query_text` to `dataset.query_endpoint`, scoped (if given) to
    `default_graph_uris`/`named_graph_uris` via the SPARQL 1.1 Protocol's
    repeatable ``default-graph-uri``/``named-graph-uri`` parameters -- the
    standard, engine-independent way to run an *unmodified* query (no
    ``GRAPH <...> { }`` rewriting needed) against a chosen subset of a
    multi-graph dataset. Returns an `rdflib.query.Result`, same shape
    `Graph.query()` returns locally: `.graph` for CONSTRUCT/DESCRIBE,
    row iteration and `.vars` for SELECT, `.askAnswer` for ASK.
    """
    form = detect_query_form(query_text)
    params: List[Tuple[str, str]] = [("query", query_text)]
    params += [("default-graph-uri", g) for g in default_graph_uris]
    params += [("named-graph-uri", g) for g in named_graph_uris]

    data = urllib.parse.urlencode(params).encode("utf-8")
    accept = _ACCEPT_FOR_FORM[form]
    request = urllib.request.Request(dataset.query_endpoint, data=data, headers=_headers(dataset, accept), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=dataset.timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise FusekiError(f"SPARQL query failed ({exc.code}): {exc.read().decode(errors='replace')}") from exc

    if form in ("CONSTRUCT", "DESCRIBE"):
        graph = Graph()
        graph.parse(data=body, format="turtle")
        result = Result("CONSTRUCT")
        result.graph = graph
        return result
    return Result.parse(source=io.BytesIO(body), format="json")


def run_update(dataset: FusekiDataset, update_text: str, using_graph_uris: Sequence[str] = ()) -> None:
    """POSTs a SPARQL 1.1 Update (e.g. one computed by `checks.repair.compute_repair`)
    to `dataset.update_endpoint`. `using_graph_uris` sets the Protocol's
    ``using-graph-uri`` parameter(s), the UPDATE-side equivalent of
    ``default-graph-uri`` -- restricts an unprefixed `WHERE` pattern in
    `update_text` to those graphs (the update's `INSERT`/`DELETE` templates
    still need their own explicit `GRAPH <...> { }` or `WITH <...>` clause
    to target a specific graph; this only scopes the *matching*)."""
    if not dataset.update_endpoint:
        raise FusekiError("FusekiDataset.update_endpoint is not set")
    params: List[Tuple[str, str]] = [("update", update_text)]
    params += [("using-graph-uri", g) for g in using_graph_uris]
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        dataset.update_endpoint, data=data, headers=_headers(dataset, "application/json"), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=dataset.timeout) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise FusekiError(f"SPARQL update failed ({exc.code}): {exc.read().decode(errors='replace')}") from exc


def list_named_graphs(dataset: FusekiDataset) -> List[str]:
    """Every named graph URI with at least one triple in `dataset` (a plain
    ``GRAPH ?g`` scan -- correct on any SPARQL 1.1 store regardless of
    Fuseki-specific graph-listing extensions)."""
    result = run_query(dataset, "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?g")
    return [str(cast(ResultRow, row)["g"]) for row in result]


def load_named_graph(dataset: FusekiDataset, graph_uri: str, limit: Optional[int] = None) -> Graph:
    """Materializes one named graph into a local `rdflib.Graph` via a single
    ``CONSTRUCT``. Pass `limit` to cap how many triples are pulled (an
    arbitrary, unsampled prefix of the graph -- for a representative sample
    of a large graph instead, materialize with a `limit` large enough to be
    useful and then run it through `reasoning.sampling.sample_graph`, or
    just materialize whole if the graph is store-side already known to be
    small, e.g. one triplify job's output)."""
    clause = f"GRAPH <{graph_uri}> {{ ?s ?p ?o }}"
    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    result = run_query(dataset, f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ {clause} }}{limit_clause}")
    assert result.graph is not None  # always set by run_query's CONSTRUCT/DESCRIBE branch
    return result.graph


def graph_predicate_and_type_usage(dataset: FusekiDataset, graph_uri: str) -> Dict[str, List[str]]:
    """Live equivalent of `sketch.prefix_alignment`'s static "what
    classes/properties does this query actually build" check, but asked of
    the real triplified named graph instead of the query text: every
    distinct ``rdf:type`` value and every distinct predicate actually
    present in `graph_uri`, right now, in the store. This is how a
    ``remote.manifest`` conformance check catches drift the static check
    can't -- e.g. a CSV column whose real values produce an unexpected IRI
    that the query template alone wouldn't reveal.
    """
    types_result = run_query(
        dataset,
        f"SELECT DISTINCT ?type WHERE {{ GRAPH <{graph_uri}> {{ ?s a ?type }} }}",
    )
    props_result = run_query(
        dataset,
        f"SELECT DISTINCT ?p WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}",
    )
    return {
        "classes": sorted({str(cast(ResultRow, row)["type"]) for row in types_result}),
        "properties": sorted({str(cast(ResultRow, row)["p"]) for row in props_result}),
    }


def run_registry_checks_remote(
    dataset: FusekiDataset,
    sparql_dir: str | Path,
    default_graph_uris: Sequence[str],
) -> Tuple[Graph, List[SparqlCheckOutcome]]:
    """Runs every `.rq` CONSTRUCT check under `sparql_dir` (the same
    registry `checks.sparql_runner.run_sparql_checks` runs locally)
    directly against `dataset`, each scoped to exactly `default_graph_uris`
    via the SPARQL Protocol -- no local graph, no download. Returns the
    same `(results_graph, outcomes)` shape as the local runner, so
    `checks.merge.build_unified_results` and the rest of the report layer
    work unchanged on either.
    """
    results = Graph()
    outcomes: List[SparqlCheckOutcome] = []
    for path in discover_queries(sparql_dir):
        check_id = path.stem
        query_text = path.read_text(encoding="utf-8")
        try:
            result = run_query(dataset, query_text, default_graph_uris=default_graph_uris)
            count = 0
            if result.graph is not None:
                for triple in result.graph:
                    results.add(triple)
                    count += 1
            outcomes.append(SparqlCheckOutcome(check_id, str(path), True, None, count))
        except Exception as exc:  # noqa: BLE001 - keep going, same convention as the local runner
            outcomes.append(SparqlCheckOutcome(check_id, str(path), False, str(exc), 0))
    return results, outcomes


def apply_repair_remote(dataset: FusekiDataset, update_text: str, using_graph_uris: Sequence[str] = ()) -> None:
    """Applies a repair's SPARQL Update (`checks.repair.RepairOutcome.update_text`
    or a hand-built one) directly to the live dataset via `run_update` --
    the remote counterpart of `checks.repair.compute_repair` mutating a
    local in-memory graph. Callers are responsible for making sure
    `update_text`'s own `INSERT`/`DELETE` templates target the intended
    named graph explicitly (e.g. wrap the template in ``WITH <graph_uri>``
    before calling this, or use the template's own `GRAPH <...> {}` if it
    already provides one) -- `using_graph_uris` only scopes the `WHERE`
    match, per SPARQL 1.1 Update semantics.
    """
    run_update(dataset, update_text, using_graph_uris=using_graph_uris)
