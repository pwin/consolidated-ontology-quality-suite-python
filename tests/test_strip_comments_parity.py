"""The comment masker, against a fixture the VS Code extension also runs.

`strip_comments` is not private plumbing. It decides which text
`--apply-repairs` is allowed to rewrite, and the extension's `stripComments`
decides the same thing for rename, find-references and go-to-definition. Both
suites shipped the same defect for the same reason -- a scan that skipped a
line beginning with `#` but read a trailing comment in full -- so renaming
`ex:Dgo` rewrote the comment that explained the rename, turning a true remark
into a false one. Both were fixed the same week, independently, in two
languages.

`test_shared_registry_parity.py` cannot see this. It compares the registry,
the shapes, the queries and the repair templates -- shared *data*, character
for character. Two hand-written ports of the same algorithm are shared
*behaviour*, and nothing compared them. The one property that matters is
exactly the one nothing checked: given the same text, both must blank the same
characters, or a rename that is safe through one tool is unsafe through the
other.

So the fixture is the statement of intent and both repos carry it. Each side
runs its own port against its own copy, which keeps this test working in CI on
a machine with only one checkout, and the last test here compares the copies
for whoever has both -- which is precisely who makes them disagree.

Every `expected` is the same length as its `input`. That is not decoration: a
comment is blanked to spaces rather than removed so that every offset found in
the mask still indexes the real document, which is what lets the caller splice
the original text around a match. A port that removed comments instead would
pass a naive equality test on the uncommented cases and corrupt every edit
after the first comment.
"""
import json
import os
from pathlib import Path

import pytest

from ontology_suite import config
from ontology_suite.sketch.bind_analysis import strip_comments

FIXTURE_NAME = "strip-comments-cases.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / FIXTURE_NAME
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]

# Where the extension keeps its copy, relative to that repo's root.
WEBAPP_FIXTURE = Path("src") / "triplify" / "__fixtures__" / FIXTURE_NAME


def _webapp_copy():
    """The extension's copy of the fixture, if this machine has that checkout.

    `ONTOLOGY_SUITE_WEBAPP` wins so the pair can live anywhere; the sibling
    directory is the layout both repos are actually developed in. Same
    resolution order as `test_shared_registry_parity.py`, which looks for the
    registry rather than this file.
    """
    configured = os.environ.get("ONTOLOGY_SUITE_WEBAPP")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(config.REPO_ROOT).parent / "consolidated_ontology_suite_webapp")
    for root in candidates:
        copy = root / WEBAPP_FIXTURE
        if copy.is_file():
            return copy
    return None


def _ids():
    return [c["name"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_the_masker_matches_the_shared_fixture(case):
    assert strip_comments(case["input"]) == case["expected"], case["why"]


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_offsets_are_preserved(case):
    """Stated separately from the equality above because it is the property
    the callers rely on, and a failure here says something different: not
    "the wrong characters were blanked" but "every position after this point
    is now a lie"."""
    assert len(strip_comments(case["input"])) == len(case["input"])


def test_the_fixture_still_covers_the_defect_that_caused_this():
    """A fixture is only a guard while the awkward cases are still in it, and
    the cheapest way to make a suite green is to delete one."""
    names = {c["name"] for c in CASES}
    assert "a trailing comment is blanked" in names
    assert "a fragment IRI keeps its local name" in names
    assert "a hash inside a double-quoted literal is not a comment" in names
    assert len(CASES) >= 16, "cases have been removed rather than added"


def test_the_extension_carries_the_same_fixture():
    """Skipped when the sibling checkout is absent, which is the common case
    in CI. Compared as parsed JSON rather than as bytes: the two repos format
    and line-end their files by their own conventions, and what has to agree
    is the cases, not the whitespace between them."""
    theirs = _webapp_copy()
    if theirs is None:
        pytest.skip(
            "the VS Code extension checkout is not on this machine "
            "(set ONTOLOGY_SUITE_WEBAPP, or check it out beside this repo)"
        )
    assert json.loads(theirs.read_text(encoding="utf-8")) == FIXTURE, (
        f"{FIXTURE_NAME} has drifted between this repo and the VS Code extension. "
        "Whichever copy is right, both must carry it -- each port is checked against "
        "its own copy, so a one-sided edit silently stops comparing them."
    )
