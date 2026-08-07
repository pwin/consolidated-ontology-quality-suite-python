"""Validates ontology_suite.versioning.diff's bump heuristic against real
released versions of Semantic Arts' gist ontology
(examples/gist_versions_reference/gistCore<version>.ttl), rather than only
small synthetic graphs.

gist's own release history already classifies each of these transitions as
a major or minor bump (per semver: an X.0.0 release is major, an X.Y.0
release with Y>0 is minor); this compares our heuristic's independent
classification against that ground truth. This also exercises the diff
tool's blank-node handling at scale: gist makes heavy use of anonymous
owl:Restriction/unionOf class expressions, whose blank-node identifiers are
never stable across two independent file parses (see ``diff._named``) --
without filtering those out, every anonymous expression looks like a
removed-then-added pair and every transition below misclassifies as MAJOR.
"""
from pathlib import Path

import pytest
from rdflib import Graph

from ontology_suite.versioning.diff import BumpLevel, diff_ontologies

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "gist_versions_reference"

# (old version, new version, expected bump per gist's own published semver)
TRANSITIONS = [
    ("10.0.0", "11.0.0", BumpLevel.MAJOR),
    ("11.0.0", "12.0.0", BumpLevel.MAJOR),
    ("12.0.0", "12.1.0", BumpLevel.MINOR),
    ("12.1.0", "13.0.0", BumpLevel.MAJOR),
    ("13.0.0", "14.0.0", BumpLevel.MAJOR),
    ("14.0.0", "14.1.0", BumpLevel.MINOR),
]


def _load(version: str) -> Graph:
    graph = Graph()
    graph.parse(FIXTURES / f"gistCore{version}.ttl", format="turtle")
    return graph


@pytest.fixture(scope="module")
def graphs():
    versions = sorted({v for pair in TRANSITIONS for v in pair[:2]})
    return {v: _load(v) for v in versions}


@pytest.mark.skipif(not FIXTURES.is_dir(), reason="gist reference ontologies not present in examples/")
@pytest.mark.parametrize("old_version,new_version,expected", TRANSITIONS)
def test_gist_transition_matches_published_semver(graphs, old_version, new_version, expected):
    diff, bump = diff_ontologies(graphs[old_version], graphs[new_version])
    assert bump == expected, (
        f"gistCore {old_version} -> {new_version}: expected {expected.value}, got {bump.value} "
        f"(removed_classes={len(diff.removed_classes)}, removed_properties={len(diff.removed_properties)}, "
        f"narrowed_domain={len(diff.narrowed_domain)}, narrowed_range={len(diff.narrowed_range)}, "
        f"added_disjoint_pairs={len(diff.added_disjoint_pairs)})"
    )


@pytest.mark.skipif(not FIXTURES.is_dir(), reason="gist reference ontologies not present in examples/")
def test_gist_snapshot_ignores_blank_nodes(graphs):
    """gist leans heavily on anonymous owl:Restriction/unionOf class
    expressions; none of them should leak into the named-class diff."""
    from rdflib.term import BNode

    from ontology_suite.versioning.diff import snapshot

    snap = snapshot(graphs["12.0.0"])
    assert not any(isinstance(c, BNode) for c in snap.classes)
    assert not any(isinstance(c, BNode) for values in snap.domain.values() for c in values)
    assert not any(isinstance(c, BNode) for values in snap.range.values() for c in values)
