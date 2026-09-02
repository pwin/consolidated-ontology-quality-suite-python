"""The check registry is shared data, and both copies of it are editable.

`resources/registry.json`, `resources/shapes/*.ttl`, `resources/sparql/**/*.rq`
and `resources/repairs/` are not this package's private tables. The same files
ship inside the VS Code extension
(`consolidated_ontology_suite_webapp/resources/checks-registry/`), which loads
them at runtime, and `ontologySuite.checksRegistryPath` can point that loader
at a checkout of *this* repo instead. So there are two copies, either one can
be edited, and nothing until now noticed when they stopped agreeing.

They had. By the time this was written the two copies differed in five
registry fields and three whole entries -- none of it deliberate, none of it
visible, and all of it discovered by diffing rather than by any test. The
worst of it was CNF-003/CNF-004: the extension's copy described a check that
reads gist-style `domainIncludes`, this copy described one that reads only
`rdfs:domain`, and the implementations differed the same way. A gist ontology
checked here got silence and the same ontology checked there got findings.

What this module pins is that the shared files are **character-for-character
identical**, not merely equivalent. Equivalence is the weaker property and the
harder one to state: a registry entry whose prose differs is already a
divergence, because the prose is what the reader sees, and it is what an
implementation is written against.

Line endings are excluded, and only those. Neither repo has a `.gitattributes`,
so `core.autocrlf` decides, and the two checkouts on the machine this was
written on already disagreed: 31 query files were CRLF here and LF there while
being otherwise identical to the character. That is git's business, not the
registry's, and a test that failed on it would be noise -- which is how a test
stops being read.

Skipped when the sibling checkout is absent, which is the common case in CI
and on a machine that only has one of the two. That makes this a test for
whoever edits both -- which is precisely who breaks it.
"""
import os
from pathlib import Path

import pytest

from ontology_suite import config

REPO_ROOT = Path(config.REPO_ROOT)


def _webapp_registry_dir():
    """The extension's copy, if this machine has one.

    `ONTOLOGY_SUITE_WEBAPP` wins so the pair can live anywhere; the sibling
    directory is the layout both repos are actually developed in.
    """
    configured = os.environ.get("ONTOLOGY_SUITE_WEBAPP")
    candidates = [Path(configured)] if configured else []
    candidates.append(REPO_ROOT.parent / "consolidated_ontology_suite_webapp")
    for root in candidates:
        registry_dir = root / "resources" / "checks-registry"
        if (registry_dir / "registry.json").is_file():
            return registry_dir
    return None


WEBAPP = _webapp_registry_dir()

pytestmark = pytest.mark.skipif(
    WEBAPP is None,
    reason="the VS Code extension checkout is not on this machine "
           "(set ONTOLOGY_SUITE_WEBAPP, or check it out beside this repo)",
)

# Query files the extension carries and this repo deliberately does not.
#
# This is not drift, it is the same check reaching the same answer by the only
# route each side has. CNF-003/CNF-004 compare a data graph against a
# *separate* ontology. This repo does that natively, in
# dataquality/data_quality.py, because its pipeline has both graphs. The
# extension merges a document with its resolved imports into one graph before
# anything runs, so it has no second graph to compare against and expresses
# the same two checks as single-graph CONSTRUCTs instead.
#
# They must not be copied here. `run_registry_suite_on_graph` runs every .rq
# in the tree against one merged graph, so a CNF-003.rq in this repo would
# fire on the ontology's own axioms and report findings the native
# implementation correctly does not -- see tests/test_check_firing_coverage.py,
# which pins CNF-* as not produced by the registry suite.
WEBAPP_ONLY_QUERIES = {"conformance/CNF-003.rq", "conformance/CNF-004.rq"}


def _tree(root, pattern):
    return {p.relative_to(root).as_posix(): p for p in root.rglob(pattern) if p.is_file()}


def _text(path):
    """Content with line endings normalised -- see the module docstring."""
    return path.read_text(encoding="utf-8")


def test_registry_json_is_identical():
    """The entries, their prose, their severities and their order.

    Order matters as much as content: both copies are read top to bottom by
    a human comparing them, and a reordered file diffs as a rewrite.
    """
    ours = _text(config.DEFAULT_REGISTRY_PATH)
    theirs = _text(WEBAPP / "registry.json")
    assert ours == theirs, (
        "registry.json has drifted between this repo and the VS Code extension. "
        "Whichever copy is right, both must carry it -- the extension can be pointed "
        "at either one."
    )


def test_shapes_are_identical():
    ours = _tree(config.DEFAULT_SHAPES_DIR, "*.ttl")
    theirs = _tree(WEBAPP / "shapes", "*.ttl")
    assert sorted(ours) == sorted(theirs), "the two copies carry different shapes files"
    differing = sorted(k for k in ours if _text(ours[k]) != _text(theirs[k]))
    assert differing == [], f"shapes files have drifted: {differing}"


def test_queries_are_identical():
    ours = _tree(config.DEFAULT_SPARQL_DIR, "*.rq")
    theirs = _tree(WEBAPP / "sparql", "*.rq")
    assert sorted(set(theirs) - set(ours)) == sorted(WEBAPP_ONLY_QUERIES), (
        "the extension carries a query file this repo does not, and it is not one of the "
        "two conformance queries that are deliberately one-sided -- either copy it here or "
        "record why it is one-sided in WEBAPP_ONLY_QUERIES"
    )
    assert sorted(set(ours) - set(theirs)) == [], (
        "this repo carries a query file the extension does not -- the extension loads the "
        "whole tree, so a missing file is a check it silently cannot run"
    )
    differing = sorted(k for k in ours if k in theirs and _text(ours[k]) != _text(theirs[k]))
    assert differing == [], f"query files have drifted: {differing}"


def test_repair_templates_are_identical():
    """The Quick Fixes. A repair whose template drifted rewrites the user's
    file differently depending on which copy answered, which is the one kind
    of drift here that edits somebody's ontology."""
    ours = _tree(config.REPO_ROOT / "ontology_suite" / "resources" / "repairs", "*")
    theirs = _tree(WEBAPP / "repairs", "*")
    assert sorted(ours) == sorted(theirs), "the two copies carry different repair templates"
    differing = sorted(k for k in ours if _text(ours[k]) != _text(theirs[k]))
    assert differing == [], f"repair templates have drifted: {differing}"


def test_every_id_the_extension_declares_is_declared_here():
    """Belt and braces on top of the whole-file comparison: if the two files are
    ever allowed to differ in formatting, this is the property that still has
    to hold, and it states it in terms of ids rather than file content."""
    import json

    ours = {c["id"] for c in json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))["checks"]}
    theirs = {c["id"] for c in json.loads((WEBAPP / "registry.json").read_text(encoding="utf-8"))["checks"]}
    assert theirs - ours == set(), f"{sorted(theirs - ours)} are declared only in the extension"
    assert ours - theirs == set(), f"{sorted(ours - theirs)} are declared only here"
