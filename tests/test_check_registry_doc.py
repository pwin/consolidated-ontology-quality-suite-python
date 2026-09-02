"""`docs/check-registry.html` is generated, but it is also *committed* --
so unlike `docs/CHECKS.md`, which anyone regenerating notices immediately,
this one can silently describe a panel the suite no longer has.

The page states counts ("50 checks", "39 SPARQL formulations"), lists every
check with its severity and remediation, and names the file each one lives
in. All of that is read from `registry.json` and the resource tree at
generation time, so the page cannot be *wrong* when it is generated -- it
can only be *stale*. These tests fail on staleness, which is the failure
mode a reviewer will not otherwise catch: add a check, forget to rerun
`python docs/generate_check_registry.py`, and the committed page keeps
advertising the old panel.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "docs" / "generate_check_registry.py"
PAGE = REPO_ROOT / "docs" / "check-registry.html"


@pytest.fixture(scope="module")
def generator():
    """Import the generator without running `main()` -- it builds the whole
    document at import time and only writes in `main()`, so `module.DOC` is
    the page as it *should* be right now."""
    spec = importlib.util.spec_from_file_location("generate_check_registry", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registry():
    from ontology_suite import config

    return json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))


def test_committed_page_is_current(generator):
    """The whole point: regenerating must be a no-op on a clean tree."""
    committed = PAGE.read_text(encoding="utf-8")
    assert committed == generator.DOC, (
        "docs/check-registry.html is out of date with registry.json / the resource tree -- "
        "run `python docs/generate_check_registry.py` and commit the result"
    )


def test_every_registered_check_is_on_the_page(generator, registry):
    """A new check must appear in the catalogue, not just in the registry."""
    missing = [c["id"] for c in registry["checks"] if f'id="{c["id"]}"' not in generator.DOC]
    assert missing == [], f"{missing} are in registry.json but absent from the page"


def test_severity_and_title_come_from_the_registry(generator, registry):
    """Guards against the prose being edited on the page instead of at its
    source -- the page is generated, so the registry is the only place a
    title or severity may be changed."""
    for check in registry["checks"]:
        block = generator.DOC[generator.DOC.index(f'id="{check["id"]}"'):][:1400]
        assert check["default_severity"] in block, f"{check['id']}: severity not rendered"
        assert check["title"] in block or check["title"].replace("&", "&amp;") in block, (
            f"{check['id']}: title not rendered"
        )


def test_headline_counts_match_the_resource_tree(generator, registry):
    """The five numbers in the masthead are load-bearing -- they are the
    first thing a reader takes away about the panel's shape.

    The last two are the pair that can quietly lie. Every registered check
    with no `.rq` file used to be counted "native Python", which was true
    while this repo was the only implementation. It stopped being true when
    the shared registry took on the ids only the VS Code extension can
    produce, and nothing about the page would have looked wrong -- it would
    simply have credited this suite with three checks it cannot run."""
    from ontology_suite import config

    rq_ids = {p.stem for p in config.DEFAULT_SPARQL_DIR.rglob("*.rq")}
    shapes = "".join(
        p.read_text(encoding="utf-8") for p in config.DEFAULT_SHAPES_DIR.glob("*.ttl")
    )
    shacl_ids = {c["id"] for c in registry["checks"] if c["id"] in shapes}

    stated = [int(n) for n in re.findall(r"<b>(\d+)</b>", generator.DOC)]
    assert stated == [
        len(registry["checks"]),
        len(rq_ids),
        len(shacl_ids),
        len(registry["checks"]) - len(rq_ids) - len(generator.EXTENSION_ONLY),
        len(generator.EXTENSION_ONLY),
    ], "masthead counts disagree with registry.json / the resource tree"


def test_extension_only_checks_are_not_advertised_as_python(generator):
    """A check this suite cannot run must not carry the Python badge. The
    badge is the only thing on the page that distinguishes them, since the
    entry, the prose and the severity are shared."""
    for cid in generator.EXTENSION_ONLY:
        block = generator.DOC[generator.DOC.index(f'id="{cid}"'):][:1800]
        assert "VS Code extension" in block, f"{cid} is not marked extension-only"
        assert ">Python</span>" not in block, f"{cid} is badged as a native Python check"


def test_page_is_self_contained(generator):
    """The page is shared as a standalone file. Google Fonts is the one
    external host allowed; anything else would render differently -- or not
    at all -- for whoever it is sent to."""
    hosts = set(re.findall(r'(?:href|src)="(https?://[^/"]+)', generator.DOC))
    assert hosts <= {"https://fonts.googleapis.com", "https://fonts.gstatic.com"}, (
        f"page references unexpected external hosts: {sorted(hosts)}"
    )


def test_every_theme_state_is_defined(generator):
    """Three states, not two: an explicit light/dark stamp, and the default
    unstamped one where only prefers-color-scheme applies. A palette defined
    for only some of them renders one theme's text on another's background."""
    doc = generator.DOC
    assert re.search(r"^:root \{", doc, re.M), "no base (light) palette"
    assert '@media (prefers-color-scheme: dark)' in doc, "no system-dark palette"
    assert ':root:not([data-theme="light"])' in doc, "system-dark not guarded against an explicit light choice"
    assert ':root[data-theme="dark"]' in doc, "no explicit-dark palette"
    assert re.search(r"body \{[^}]*background: var\(--ground\)", doc, re.S), (
        "body must paint an explicit background token, or it borrows the host's theme"
    )
