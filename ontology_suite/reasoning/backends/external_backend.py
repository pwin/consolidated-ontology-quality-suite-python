"""Best-effort external OWL2 DL reasoner backend via ``owlready2`` (HermiT by
default, or Pellet). This is an *optional* dependency (the ``reasoner`` extra
in pyproject.toml) -- HermiT/Pellet themselves are Java processes owlready2
shells out to, so a working Java runtime is also required. Neither is
guaranteed to be present; every entry point here degrades to returning
``None`` (import unavailable) or a single informational finding (reasoner
present but failed to run) rather than raising, so callers can always fall
back to the always-on owlrl backend.

Unlike ``owlrl_backend.py``'s rule-based closure, a real DL reasoner is
complete for full OWL2 DL: it can prove a class unsatisfiable even when no
RL rule fires.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import List, Optional

from rdflib import Graph

# `RUN_LEVEL_FOCUS` is the suite-wide anchor for a finding about the graph
# as a whole rather than about a term; it is defined next to `ResultRow`
# because the report layer is what has to recognise it. Imported rather
# than spelled again here -- these two rows and `cli`'s namespace filter
# disagreeing about it is the defect that dropped REA-022 from every
# scoped run.
from ...checks.merge import RUN_LEVEL_FOCUS, ResultRow


def available() -> bool:
    try:
        import owlready2  # noqa: F401
    except ImportError:
        return False
    return True


def _inconsistent_ontology_row(reasoner: str) -> ResultRow:
    return ResultRow(
        check_id="REA-020",
        category="reasoning",
        title="Ontology found inconsistent by an external DL reasoner",
        severity="Violation",
        focus_node=RUN_LEVEL_FOCUS,
        path=None,
        value=None,
        message=(
            "The ontology (plus data, if included) is logically inconsistent according to the "
            f"{reasoner} reasoner: no model satisfies every axiom simultaneously."
        ),
        remediation=(
            "Consult the reasoner's explanation/justification support to find the minimal "
            "contradicting axiom set; owlrl's pattern-based REA-00x checks may already surface "
            "part of the same contradiction."
        ),
        sources=["external-reasoner"],
    )


def _blame(owl_path: Path, detail: str) -> str:
    """``detail`` with the offending line of the serialised ontology appended.

    owlready2 reports a parse failure as a position in the file it was handed
    -- "RDF/XML parsing error in file .../tmp8f3k/ontology.owl, line 36,
    column 97" -- and that file is a temporary serialisation of the graph,
    which no longer exists by the time anyone reads the report. The line
    number refers to a document the user never saw.

    The temporary directory is still alive inside the `except` block that calls
    this, so the line can be read back and quoted. It names the entity: a
    reader learns that `ex:thing ex:count "twelve"` is what defeated the
    reasoner, rather than a column in a vanished file.

    Best-effort by construction. If the message carries no position, or the
    file cannot be read, the original detail is returned unchanged -- a worse
    message is better than an exception while building an error report.
    """
    match = re.search(r"line ([0-9]+)", detail)
    if not match:
        return detail
    try:
        lines = owl_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return detail

    number = int(match.group(1))
    if not (1 <= number <= len(lines)):
        return detail
    quoted = lines[number - 1].strip()
    if not quoted:
        return detail
    return (
        f"{detail}. That line of the serialised ontology reads: {quoted} "
        f"(the file is a temporary copy of the graph, so the line number alone "
        f"names nothing you can open)"
    )


def unavailable_row(reasoner: str, detail: str) -> ResultRow:
    return ResultRow(
        check_id="REA-022",
        category="reasoning",
        title="External DL reasoner unavailable -- only owlrl-based checks ran",
        severity="Info",
        focus_node=RUN_LEVEL_FOCUS,
        path=None,
        value=None,
        message=(
            f"The external DL reasoner ('{reasoner}') could not be run: {detail}. "
            "Only the always-on owlrl RDFS/OWL2-RL closure and pattern checks (REA-001..004, "
            "LOG-001..007) ran. These are sound but not complete for full OWL2 DL -- a class can be "
            "genuinely unsatisfiable without any of them firing."
        ),
        remediation=(
            "Install the 'reasoner' extra (`uv sync --extra reasoner`, i.e. owlready2) and ensure a "
            "Java runtime is on PATH if you need complete OWL2 DL consistency/satisfiability checking; "
            "see docs/REASONING.md."
        ),
        sources=["external-reasoner"],
    )


def run_external_reasoner(graph: Graph, reasoner: str = "hermit") -> Optional[List[ResultRow]]:
    """Run a real OWL2 DL reasoner over ``graph`` via owlready2.

    Returns ``None`` if owlready2 itself isn't importable -- callers should
    treat that as "backend unavailable" and decide how to report it. If
    owlready2 is importable but the reasoner invocation itself fails (e.g. no
    Java runtime), a single ``REA-022`` informational row is returned instead
    of raising. Otherwise returns ``REA-020``/``REA-021`` rows for overall
    inconsistency / unsatisfiable classes.
    """
    try:
        import owlready2
    except ImportError:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        owl_path = Path(tmp) / "ontology.owl"
        graph.serialize(destination=str(owl_path), format="xml")

        try:
            world = owlready2.World()
            # Deliberately a plain filesystem path, not `owl_path.as_uri()`:
            # owlready2 mishandles a `file:///C:/...` URI on Windows (it
            # strips the `file://` scheme naively, leaving an invalid
            # `/C:/...` path with a stray leading slash before the drive
            # letter, which then fails with `OSError: Invalid argument`).
            # owlready2's get_ontology() accepts a plain local path directly.
            onto = world.get_ontology(str(owl_path)).load()
            with onto:
                if reasoner == "pellet":
                    owlready2.sync_reasoner_pellet(world, infer_property_values=True)
                else:
                    owlready2.sync_reasoner(world, infer_property_values=True)
        except owlready2.OwlReadyInconsistentOntologyError:
            # Not a tooling failure -- this *is* the answer: the reasoner
            # proved there's no model satisfying every axiom at once. Report
            # it as REA-020 directly rather than falling through to the
            # generic "reasoner unavailable" path (world.inconsistent_classes()
            # can't be queried after this error, so there's nothing more to add).
            return [_inconsistent_ontology_row(reasoner)]
        except Exception as exc:  # noqa: BLE001 - any other failure is genuinely a tooling/environment issue
            # `_blame` while the temp directory is still alive: it reads back
            # the line owlready2 complained about, which is gone once this
            # `with` block exits.
            return [unavailable_row(reasoner, _blame(owl_path, str(exc)))]

        rows: List[ResultRow] = []
        try:
            inconsistent = list(world.inconsistent_classes())
        except Exception as exc:  # noqa: BLE001
            return [unavailable_row(reasoner, f"reasoner ran but result inspection failed: {exc}")]

        # Only `owl:Thing`. An unsatisfiable `owl:Thing` means every
        # individual is impossible, which is inconsistency; `owl:Nothing`
        # being unsatisfiable is a tautology, and owlready2 lists it whenever
        # *any* class is unsatisfiable.
        #
        # Testing for either reported REA-020 ("ontology found inconsistent")
        # on an ontology that was merely carrying one impossible class.
        # Measured on three ontologies: clean gave nothing; one unsatisfiable
        # class gave `inconsistent_classes() == [ex:Impossible, owl:Nothing]`
        # and so both REA-021 (right) and REA-020 (wrong); a genuinely
        # inconsistent ontology never reaches here at all, because owlready2
        # raises OwlReadyInconsistentOntologyError and the handler above
        # returns REA-020 on its own.
        #
        # The harm was misdirection rather than a wrong exit status: REA-021
        # is a Violation too, so the gate failed either way. But
        # inconsistency sends a reader to the ABox -- which individual
        # asserts the contradiction -- and unsatisfiability sends them to one
        # class's own axioms. Two different searches, and the finding named
        # the wrong one.
        #
        # OwlReadyInconsistentOntologyError is the normal signal. This stays
        # as belt-and-braces because owlready2's own docs note Thing can
        # surface here directly in some cases.
        thing_or_nothing = {owlready2.Thing, owlready2.owl.Nothing}
        if owlready2.Thing in inconsistent:
            rows.append(_inconsistent_ontology_row(reasoner))

        for cls in inconsistent:
            if cls in thing_or_nothing:
                continue
            iri = getattr(cls, "iri", str(cls))
            rows.append(ResultRow(
                check_id="REA-021",
                category="reasoning",
                title="Class found unsatisfiable by an external DL reasoner",
                severity="Violation",
                focus_node=str(iri),
                path=None,
                value=None,
                message=f"{iri} is unsatisfiable (equivalent to owl:Nothing) according to the {reasoner} reasoner.",
                remediation="Review the class's superclass/disjointness/restriction axioms for a direct contradiction.",
                sources=["external-reasoner"],
            ))
        return rows
