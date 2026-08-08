"""Shared path resolution for the suite: where the registry/shapes/sparql
checks live relative to this package, and where to find the sibling tools
this suite shells out to or points users at (``oxi-gen``, and the companion
``turtle-editor-viewer``) rather than vendoring.

The registry/shapes/sparql/templates/repairs resources are bundled *inside*
the ``ontology_suite`` package itself (``ontology_suite/resources/``), not
alongside it at the repo root -- they're load-bearing at runtime for
`checks`/`run`/`docgen`/repair-suggestion functionality, so they need to
actually ship with an installed wheel (``pip install .``, ``pip install
git+...``, or a future PyPI release), not just exist in a git checkout.
``examples/`` stays at the repo root: it's a dev/test convenience only,
nothing in the installed package depends on it existing at runtime.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_RESOURCES = Path(__file__).resolve().parent / "resources"

DEFAULT_SHAPES_DIR = PACKAGE_RESOURCES / "shapes"
DEFAULT_SPARQL_DIR = PACKAGE_RESOURCES / "sparql"
DEFAULT_REGISTRY_PATH = PACKAGE_RESOURCES / "registry.json"
DEFAULT_EXAMPLES_DIR = REPO_ROOT / "examples"
DEFAULT_DOCGEN_TEMPLATE = PACKAGE_RESOURCES / "templates" / "documentation-template.html"
DEFAULT_REPAIRS_DIR = PACKAGE_RESOURCES / "repairs"

# oxi-gen is referenced as a sibling repo (same parent directory as this
# one), not vendored -- see docs/ARCHITECTURE.md.
DEFAULT_OXI_GEN_REPO = REPO_ROOT.parent / "oxi-gen"

# turtle-editor-viewer is a companion tool for interactively browsing/
# querying the .ttl artifacts this suite reads and writes (ontology, sketch,
# triplified data). Referenced by source at this sibling path, but there's
# also a hosted instance the report layer links to directly -- no local
# build/serve step needed to use it.
DEFAULT_TURTLE_EDITOR_VIEWER_REPO = REPO_ROOT.parent / "turtle-editor-viewer"
TURTLE_EDITOR_VIEWER_URL = "https://semantechs.co.uk/turtle-editor-viewer-new/"

_OXI_GEN_BINARY_NAMES = ("oxi_gen.exe", "oxi_gen")


def find_oxi_gen_binary(explicit: Optional[str | Path] = None) -> Optional[Path]:
    """Resolve the built ``oxi-gen`` binary, in order of preference:

    1. ``explicit`` (typically the CLI's ``--oxi-gen-bin`` flag)
    2. the ``ONTOLOGY_SUITE_OXI_GEN_BIN`` environment variable
    3. ``target/release/oxi_gen(.exe)`` under ``DEFAULT_OXI_GEN_REPO``
       (the sibling ``oxi-gen`` checkout, built with ``cargo build --release``)
    4. ``oxi_gen``/``oxi_gen.exe`` on ``PATH``

    Returns ``None`` if none of the above resolve to an existing file --
    callers should surface a clear "build oxi-gen first" error rather than
    fail with a confusing subprocess error.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_bin = os.environ.get("ONTOLOGY_SUITE_OXI_GEN_BIN")
    if env_bin:
        candidates.append(Path(env_bin))
    for name in _OXI_GEN_BINARY_NAMES:
        candidates.append(DEFAULT_OXI_GEN_REPO / "target" / "release" / name)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    for name in _OXI_GEN_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)

    return None
