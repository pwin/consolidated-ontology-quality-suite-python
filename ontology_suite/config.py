"""Shared path resolution for the suite: where the registry/shapes/sparql
checks live relative to this package, and where to find the companion
``turtle-editor-viewer`` the reports point users at.

There used to be a third job here: finding a built ``oxi-gen`` binary for
`triplify` to shell out to. tarka, from ``semops-system``, replaced it in
0.29.0 and needs no binary -- see ``triplify/tarka.py``.

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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_RESOURCES = Path(__file__).resolve().parent / "resources"

DEFAULT_SHAPES_DIR = PACKAGE_RESOURCES / "shapes"
DEFAULT_SPARQL_DIR = PACKAGE_RESOURCES / "sparql"
DEFAULT_REGISTRY_PATH = PACKAGE_RESOURCES / "registry.json"
DEFAULT_EXAMPLES_DIR = REPO_ROOT / "examples"
DEFAULT_DOCGEN_TEMPLATE = PACKAGE_RESOURCES / "templates" / "documentation-template.html"
DEFAULT_REPAIRS_DIR = PACKAGE_RESOURCES / "repairs"

# turtle-editor-viewer is a companion tool for interactively browsing/
# querying the .ttl artifacts this suite reads and writes (ontology, sketch,
# triplified data). Referenced by source at this sibling path, but there's
# also a hosted instance the report layer links to directly -- no local
# build/serve step needed to use it.
DEFAULT_TURTLE_EDITOR_VIEWER_REPO = REPO_ROOT.parent / "turtle-editor-viewer"
TURTLE_EDITOR_VIEWER_URL = "https://semantechs.co.uk/turtle-editor-viewer-new/"
