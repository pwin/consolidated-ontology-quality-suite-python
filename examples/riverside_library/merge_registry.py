"""Join Riverside's four checks onto the suite's own registry.

WHY THIS SCRIPT EXISTS
----------------------
`--sparql` can be given more than once, so your queries run *alongside* the
suite's. `--registry` cannot: it takes one file, and that file replaces the
suite's registry completely.

So if you pass only your own registry, your four checks resolve and the
suite's 61 do not. Their findings still appear -- they are discovered from
the filesystem, not from the registry -- but each one arrives with no id, no
title and no remediation, printed as UNMAPPED. The run looks broken when it
is merely misconfigured.

The answer is to hand `--registry` a merged file, which is what this script
writes.

WHY IT MERGES AT RUN TIME INSTEAD OF YOU COPYING THE FILE
---------------------------------------------------------
It would be quicker to copy the suite's registry.json once, paste your four
entries at the bottom, and commit the result. Do not do that.

The moment you copy it, the suite's entries freeze at the version you
copied. When the suite adds a check, improves a title, or corrects a piece
of remediation advice, your copy keeps the old text and nothing tells you.
Reading `config.DEFAULT_REGISTRY_PATH` at run time means you always get the
installed suite's current entries, and your fragment only ever holds the
four checks you actually own.

USAGE
-----
    uv run python examples/riverside_library/merge_registry.py

It writes `out/riverside/merged-registry.json` and prints what it did. Run
it again after upgrading the suite; it is cheap and idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path

from ontology_suite import config

HERE = Path(__file__).parent
FRAGMENT = HERE / "house_checks" / "registry-fragment.json"

# Written under out/ rather than next to the fragment because it is a
# generated file: it should not be committed, and it should not be edited by
# hand. It does need to be somewhere a person can point a command at, though,
# because reproducing a LIB-00x finding from the command line needs it.
OUTPUT = Path("out") / "riverside" / "merged-registry.json"


def merge() -> Path:
    """Write the installed registry plus Riverside's fragment, and return the path."""
    shipped = json.loads(Path(config.DEFAULT_REGISTRY_PATH).read_text(encoding="utf-8"))
    local = json.loads(FRAGMENT.read_text(encoding="utf-8"))

    shipped_ids = {entry["id"] for entry in shipped["checks"]}
    local_ids = {entry["id"] for entry in local["checks"]}

    # An id collision would be silent and nasty: two entries with the same id,
    # and whichever the lookup happens to reach decides the title and severity
    # your report shows. Cheap to detect, so detect it.
    clash = shipped_ids & local_ids
    if clash:
        raise SystemExit(
            f"{FRAGMENT}: these check ids are already used by the suite: {sorted(clash)}. "
            "Rename yours -- a house check should use your own prefix (LIB-, ACME-, ...) "
            "so that a report can always tell your rules from the suite's."
        )

    shipped["checks"] = shipped["checks"] + local["checks"]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(shipped, indent=2), encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    path = merge()
    total = len(json.loads(path.read_text(encoding="utf-8"))["checks"])
    local_count = len(json.loads(FRAGMENT.read_text(encoding="utf-8"))["checks"])
    print(f"wrote {path}")
    print(f"  {total - local_count} checks from the installed suite")
    print(f"  {local_count} from {FRAGMENT.relative_to(HERE.parent.parent)}")
    print(f"  {total} in total -- pass this file to --registry")
