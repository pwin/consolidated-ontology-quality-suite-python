"""Build one shapes directory holding the suite's shapes and Riverside's.

WHY THIS SCRIPT EXISTS, AND WHY IT IS NOT merge_registry.py
-----------------------------------------------------------
`--sparql` can be given more than once, so adding a query is additive: name
your directory and `@builtin`, and both run.

`--shapes` cannot. It takes one directory, and the engine loads every `*.ttl`
in it into a single shapes graph. So `--shapes house_checks/shapes` does not
add your shape to the suite's -- it replaces all six of the suite's files with
your one, and the SHACL arm of all 21 shape-backed checks stops running.

Measured on this example with `--engine shacl`: 37 findings with the installed
shapes, 1 with `house_checks/shapes` alone. From suite 0.26.0 the run warns
when that happens; before it, nothing did.

There is no flag that composes, so the merge is a directory rather than an
argument. This script writes that directory.

WHY IT COPIES AT RUN TIME
-------------------------
The same reason `merge_registry.py` merges at run time: a committed copy of
the suite's six shape files freezes them at the version you copied, and
nothing tells you when the suite improves one. Reading
`config.DEFAULT_SHAPES_DIR` means you always get the installed version.

The output is a directory of copies rather than symlinks, because symlinks
need a privilege on Windows that an ordinary user does not have.

ONE SUBTLETY WORTH KNOWING BEFORE YOU WRITE A SHAPE
---------------------------------------------------
If the check already has a `.rq` twin -- as `LIB-001` does -- the two arms
must report the *same* focus node, path and value, because that tuple is the
deduplication key. A `sh:property` shape reports `sh:resultPath`, and the
`.rq` reports none, so the same defect arrives as two rows under one id.
`house_checks/shapes/house.ttl` is therefore written as SHACL-SPARQL, which is
why the suite's own dual-formulated shapes are too.

WHICH ENGINE ANSWERS WHAT
-------------------------
The counts this script prints come from `ontology_suite.checks.shapes`, and
they are not all computed the same way, on purpose:

* rdflib **parses** the `*.ttl` files, which is the job `holos_sparql.py`
  keeps it for.
* holosdb **answers the query** "which check ids does this directory
  declare". The suite moved its checks off rdflib's graph API for the reason
  recorded in that module -- rdflib is lenient where the specification is not
  -- and a number printed to you deserves the same engine the checks get.
* the `shacl` engine **confirms it will compile the merged directory**. That
  is the question the other two cannot answer: a file rdflib parses happily
  may still be one the engine refuses, and then this script would print "22
  shape-backed checks" for a directory that validates nothing. It is checked
  here, at the merge, rather than discovered three commands later.

pyshacl is not involved anywhere. It is a development dependency of the
suite, for the engine-parity tests, and a released `pip install` does not
have it.

USAGE
-----
    uv run python examples/riverside_library/merge_shapes.py

It writes `out/riverside/shapes/` and prints what it did.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ontology_suite import config
from ontology_suite.checks.shapes import declared_check_ids, engine_rejection

HERE = Path(__file__).parent
HOUSE = HERE / "house_checks" / "shapes"
OUTPUT = Path("out") / "riverside" / "shapes"


def merge() -> Path:
    """Write the installed shapes plus Riverside's into one directory."""
    if OUTPUT.exists():
        # Rebuilt from scratch rather than copied over: a shape file deleted
        # upstream, or renamed, would otherwise linger here and keep running.
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)

    shipped = sorted(config.DEFAULT_SHAPES_DIR.glob("*.ttl"))
    house = sorted(HOUSE.glob("*.ttl"))

    clash = {p.name for p in shipped} & {p.name for p in house}
    if clash:
        raise SystemExit(
            f"{HOUSE}: these file names collide with the suite's shape files: "
            f"{sorted(clash)}. One would overwrite the other in the merged "
            "directory, and which depends on copy order. Rename yours."
        )

    for path in shipped + house:
        shutil.copyfile(path, OUTPUT / path.name)
    return OUTPUT


if __name__ == "__main__":
    path = merge()

    # Asked first. Every count below is read off the parse, so printing them
    # for a directory the engine rejects would be worse than printing nothing.
    rejection = engine_rejection(path)
    if rejection is not None:
        raise SystemExit(
            f"{path} parses, but the SHACL engine will not compile it:\n"
            f"  {rejection}\n"
            "The merged directory is on disk; the fault is in one of the files "
            "copied into it."
        )

    theirs = declared_check_ids(config.DEFAULT_SHAPES_DIR)
    mine = declared_check_ids(HOUSE)
    print(f"wrote {path}")
    print(f"  {len(list(config.DEFAULT_SHAPES_DIR.glob('*.ttl')))} shape file(s) from the "
          f"installed suite, declaring {len(theirs)} checks")
    print(f"  {len(list(HOUSE.glob('*.ttl')))} from house_checks/shapes, "
          f"declaring {len(mine)}: {', '.join(sorted(mine))}")
    print(f"  {len(theirs | mine)} shape-backed checks in total -- pass this directory to --shapes")
    print("  the `shacl` engine compiles it (checked, not assumed)")
