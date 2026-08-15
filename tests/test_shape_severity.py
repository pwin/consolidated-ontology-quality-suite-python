"""Guards where `sh:severity` is declared in `resources/shapes/*.ttl`.

SHACL defines `sh:severity` as a property of a *shape*. It is legal Turtle
to put it on the `sh:SPARQLConstraint` blank node inside an `sh:sparql [
... ]` block instead -- and every shape in this suite used to -- but no
processor is obliged to look there, and pyshacl does not: it falls back to
the spec default, `sh:Violation`, and reports every SHACL-sourced finding
at that severity no matter what the shape says. The native engine *does*
read the nested form, so the two engines returned different severities for
the same findings from the same shapes.

That made `--fail-on Violation` (every subcommand's default) depend on
which `--engine` was passed: under pyshacl a class named `person_record`
(`STY-001`, registry default `Warning`) failed a CI gate exactly as hard as
a logical contradiction. It also meant the same finding could appear twice
at two severities in one report, since `checks/merge.py`'s dedup key
distinguishes rows that differ in nothing but severity's downstream
effects.

Both tests below are about *placement*, not about any one engine's
behavior, because placement is the thing that made the engines disagree:

* every check id implemented as a shape declares exactly one severity, and
  it matches that check's `default_severity` in `registry.json`;
* no `sh:severity` sits inside an `sh:sparql [ ... ]` block.

`sh:severity` inside `sh:property [ ... ]` (LOG-001, LOG-003) is correct
and deliberately left alone -- a property shape *is* a shape, and every
engine reads it.
"""
import json
import re

import pytest
from rdflib import Graph, Namespace
from rdflib.namespace import RDF

from ontology_suite import config
from ontology_suite.checks.shacl_runner import load_shapes_graph

SH = Namespace("http://www.w3.org/ns/shacl#")
OQ = Namespace("https://semantechs.co.uk/ontology-quality/")

REGISTRY = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
DEFAULT_SEVERITY = {c["id"]: c["default_severity"] for c in REGISTRY["checks"]}

# `sh:sparql [` ... up to the matching `] ;`/`] .` that closes it. The
# shapes are hand-formatted with the closing bracket at 2-space indent, so
# a non-greedy match to the next line that starts one is exact enough to
# tell "inside the SPARQLConstraint block" from "on the shape".
SPARQL_BLOCK = re.compile(r"sh:sparql \[.*?^  \]", re.DOTALL | re.MULTILINE)


def _shape_check_id(shapes: Graph, shape) -> str | None:
    """The registry id a shape node belongs to -- by its own local name
    (`oq:STY-001`), or by an explicit `oq:checkId` annotation on shapes that
    had to be split into several nodes (`oq:STY-002-obj`/`-data`). The same
    two conventions `registry.py::resolve_check_id` uses."""
    local = str(shape).rsplit("/", 1)[-1]
    if local in DEFAULT_SEVERITY:
        return local
    annotated = shapes.value(shape, OQ.checkId)
    return str(annotated) if annotated is not None else None


@pytest.fixture(scope="module")
def shapes() -> Graph:
    return load_shapes_graph(config.DEFAULT_SHAPES_DIR)


def test_every_shape_declares_its_registry_default_severity(shapes):
    """A shape that declares no severity at all silently gets sh:Violation
    from the SHACL spec default, which is wrong for 13 of the 18 checks
    implemented as shapes -- so "declared, and equal to the registry" is the
    assertion, not just "equal if declared"."""
    mismatches = []
    for shape in shapes.subjects(RDF.type, SH.NodeShape):
        check_id = _shape_check_id(shapes, shape)
        if check_id is None:
            continue
        expected = DEFAULT_SEVERITY[check_id]
        declared = [
            str(s).rsplit("#", 1)[-1]
            # Either directly on the NodeShape, or on the nested property
            # shape a SHACL-core check's constraint lives in (LOG-001,
            # LOG-003) -- both are shapes, both are read by every engine.
            for node in [shape, *shapes.objects(shape, SH.property)]
            for s in shapes.objects(node, SH.severity)
        ]
        if declared != [expected]:
            mismatches.append(f"{check_id} ({shape}): declares {declared or 'nothing'}, registry says {expected}")
    assert mismatches == [], "shape severity out of step with registry.json:\n  " + "\n  ".join(mismatches)


def test_no_severity_is_declared_inside_a_sparql_constraint_block():
    """The specific placement pyshacl drops. Asserted against the file text
    rather than the parsed graph because, once parsed, an `sh:severity` on
    the SPARQLConstraint blank node and one on the shape are two triples
    that look equally reasonable -- it is the nesting that is the bug."""
    offenders = []
    for path in sorted(config.DEFAULT_SHAPES_DIR.glob("*.ttl")):
        text = path.read_text(encoding="utf-8")
        for block in SPARQL_BLOCK.finditer(text):
            if "sh:severity" in block.group(0):
                line = text[: block.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
    assert offenders == [], (
        "sh:severity declared inside an sh:sparql [...] SPARQLConstraint block at "
        f"{offenders} -- pyshacl ignores it there and reports sh:Violation instead; "
        "move it onto the enclosing shape node"
    )
