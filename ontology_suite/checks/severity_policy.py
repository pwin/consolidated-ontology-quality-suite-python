"""Stage-dependent severity, declared in one file and applied at the edge.

The problem this solves is not "I want a green build". It is that the same
fault genuinely means different things at different points in a project's
life: a property with no domain or range is worth knowing about while you are
sketching a model and is worth blocking a release over, and nothing about the
check changes between those two readings -- only the stringency being applied.

``CLAUDE.md`` says, and means: *set severity to what is true and use
``--fail-on`` to decide what blocks a build; do not relabel a finding to get a
green build.* That rule is what makes this module's shape what it is:

* ``--fail-on`` is one global threshold. It cannot say "structural faults
  block, style does not", so it is the right instrument for *how strict is
  this stage* and the wrong one for *which checks does this stage care
  about*. Both are needed, and conflating them is what drives people to edit
  the checks themselves.
* A check's declared severity is still the truth, so it is never lost.
  ``ResultRow.declared_severity`` keeps what the check said whenever a policy
  changed it, ``full_results.csv`` has a column for it, and every re-mapping
  is printed on the run that did it. A policy cannot quietly relabel anything:
  the relabelling is in the report, attributable to a named stage in a named
  file.

So this is the auditable form of the thing people were doing anyway. The
alternative, measured in the testing repository before this existed, was to
copy the suite's ``sparql/`` and ``shapes/`` trees and rewrite
``sh:resultSeverity`` and ``sh:severity`` in place. That works and is awful:
it couples a project to how the suite spells severity, it freezes the checks
at the version they were copied from, and -- worst -- applying it to only one
of the two formulations silently discards the policy, because the shape's
severity wins and nothing reports the disagreement.

Nothing here touches a check file. The policy is resolved against the registry
the run loaded and applied to finished rows, which is also why it works for
checks the suite never shipped: a project-local check merged into the registry
is an entry like any other, and ``FLT-001`` is as addressable as ``STR-002``.

File shapes
-----------

The canonical form names its stages, and the names are yours -- nothing here
knows or prefers any particular set::

    {
      "stages": {
        "sketch":  { "STR-003": "Info" },
        "draft":   { "STR-003": "Warning" },
        "release": { "extends": "draft", "STR-003": "Violation",
                     "QUA-004": "off" }
      }
    }

``extends`` pulls in another stage and then applies the rest on top, so three
stages that differ in two checks do not have to restate the other forty.

A file with no stages at all is read as a single stage, so the simplest useful
policy is a flat object, and a list of single-key objects is accepted too
because that is what people write by hand::

    { "STR-002": "Info" }
    [ { "STR-002": "Info" }, { "STR-003": "Violation" } ]

``off`` is not a severity -- the suite has exactly three -- it is this file's
way of saying "do not ask this question at this stage", and it drops the
check's findings from the report. It is the honest spelling of "not a worry",
and it is deliberately distinct from ``Info``: ``Info`` means *we looked and
here it is*, ``off`` means *we did not ask*.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SEVERITIES = ("Violation", "Warning", "Info")
OFF = "off"

# Reserved inside a stage's map. A check id is an uppercase prefix, a dash and
# digits, so this cannot collide with one -- and an id that did look like this
# would be rejected by validate() against the registry anyway.
EXTENDS = "extends"

IMPLICIT_STAGE = "default"


class PolicyError(Exception):
    """A policy that cannot be used. Carries the message the CLI prints.

    Raised rather than returned because every caller's answer is the same --
    stop, say why -- and because a half-applied severity policy is the one
    outcome this module exists to make impossible.
    """


@dataclass(frozen=True)
class Change:
    """One check's re-mapping, for the audit line and the tests."""

    check_id: str
    declared: str
    applied: str
    rows: int

    @property
    def suppressed(self) -> bool:
        return self.applied == OFF

    def describe(self) -> str:
        if self.suppressed:
            return f"{self.check_id} {self.declared} -> off ({self.rows} finding(s) dropped)"
        return f"{self.check_id} {self.declared} -> {self.applied} ({self.rows} finding(s))"


@dataclass
class SeverityPolicy:
    path: Path
    stages: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "SeverityPolicy":
        path = Path(path)
        if not path.is_file():
            raise PolicyError(f"no severity policy at {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PolicyError(f"{path} is not valid JSON: {exc}") from exc

        if isinstance(raw, dict) and "stages" in raw:
            stages_raw = raw["stages"]
            if not isinstance(stages_raw, dict) or not stages_raw:
                raise PolicyError(f"{path}: 'stages' must be a non-empty object")
            stages = {
                name: cls._stage_map(path, name, body)
                for name, body in stages_raw.items()
            }
        else:
            stages = {IMPLICIT_STAGE: cls._stage_map(path, IMPLICIT_STAGE, raw)}

        policy = cls(path=path, stages=stages)
        policy._check_extends()
        return policy

    @staticmethod
    def _stage_map(path: Path, name: str, body) -> Dict[str, str]:
        """One stage's id -> severity map, from an object or a list of them."""
        if isinstance(body, dict):
            items = list(body.items())
        elif isinstance(body, list):
            items = []
            for entry in body:
                if not isinstance(entry, dict):
                    raise PolicyError(
                        f"{path}: stage '{name}' has an entry that is not an "
                        f"object: {entry!r}"
                    )
                items.extend(entry.items())
        else:
            raise PolicyError(
                f"{path}: stage '{name}' must be an object or a list of "
                f"objects, found {type(body).__name__}"
            )

        mapping: Dict[str, str] = {}
        for key, value in items:
            if key in mapping and mapping[key] != value:
                # Silently keeping the last would make the file's meaning
                # depend on its order, which is not a thing anyone should have
                # to know about a policy.
                raise PolicyError(
                    f"{path}: stage '{name}' lists {key} twice, as "
                    f"{mapping[key]!r} and as {value!r}"
                )
            mapping[key] = value
        return mapping

    def _check_extends(self) -> None:
        for name in self.stages:
            seen = [name]
            cursor = name
            while True:
                parent = self.stages[cursor].get(EXTENDS)
                if parent is None:
                    break
                if parent not in self.stages:
                    raise PolicyError(
                        f"{self.path}: stage '{cursor}' extends '{parent}', "
                        f"which is not a stage here. Stages: "
                        f"{', '.join(sorted(self.stages))}"
                    )
                if parent in seen:
                    raise PolicyError(
                        f"{self.path}: stages extend each other in a cycle: "
                        f"{' -> '.join(seen + [parent])}"
                    )
                seen.append(parent)
                cursor = parent

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def stage_names(self) -> List[str]:
        return sorted(self.stages)

    def resolve(self, stage: Optional[str]) -> Tuple[str, Dict[str, str]]:
        """The effective id -> severity map for one stage, ``extends`` applied.

        A policy with one stage does not need naming, so ``--severity-policy``
        alone is enough for the common case. A policy with several does: the
        alternative is picking one for the caller, and picking the wrong
        stringency silently is the failure this whole module is against.
        """
        if stage is None:
            if len(self.stages) == 1:
                stage = next(iter(self.stages))
            else:
                raise PolicyError(
                    f"{self.path} declares {len(self.stages)} stages "
                    f"({', '.join(self.stage_names())}); say which one with "
                    f"--stringency"
                )
        if stage not in self.stages:
            raise PolicyError(
                f"{self.path} has no stage '{stage}'. Stages: "
                f"{', '.join(self.stage_names())}"
            )

        # Walk to the root of the extends chain, then apply each stage's own
        # entries on the way back down, so the nearest wins.
        chain = [stage]
        cursor = stage
        while True:
            parent = self.stages[cursor].get(EXTENDS)
            if parent is None:
                break
            chain.append(parent)
            cursor = parent

        resolved: Dict[str, str] = {}
        for name in reversed(chain):
            for key, value in self.stages[name].items():
                if key != EXTENDS:
                    resolved[key] = value
        return stage, resolved

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @staticmethod
    def validate(resolved: Dict[str, str], known_ids) -> None:
        """Reject a resolved stage before it is applied, naming every problem.

        Both failure modes are silent otherwise, and both have been seen: a
        severity the suite does not have cannot be applied to anything, and an
        id the registry does not have matches no finding, so the override is
        simply never applied. ``STR-0003`` for ``STR-003`` is the one to
        expect -- it reads as entirely plausible in a diff.
        """
        known = set(known_ids)
        problems: List[str] = []
        for check_id, severity in sorted(resolved.items()):
            if check_id not in known:
                near = sorted(
                    other for other in known if _squash(other) == _squash(check_id)
                )
                hint = f" -- did you mean {', '.join(near)}?" if near else ""
                problems.append(
                    f"{check_id} is not a check in this registry{hint}"
                )
            if severity not in SEVERITIES and severity != OFF:
                problems.append(
                    f"{check_id}: {severity!r} is not a severity. Use one of "
                    f"{', '.join(SEVERITIES)}, or {OFF!r} to leave the check "
                    f"out of this stage"
                )
        if problems:
            raise PolicyError("; ".join(problems))

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    @staticmethod
    def apply(rows, resolved: Dict[str, str]) -> Tuple[list, List[Change]]:
        """Re-map finished rows, keeping what each check declared.

        Applied to rows rather than to checks, which is what makes it work for
        every arm at once: a check written as both a shape and a query comes
        back as one row by the time this sees it, so there is no way to apply
        the policy to half of it.
        """
        counts: Dict[Tuple[str, str], int] = {}
        kept = []
        for row in rows:
            target = resolved.get(row.check_id) if row.check_id else None
            if target is None:
                kept.append(row)
                continue
            declared = row.severity
            counts[(row.check_id, declared)] = counts.get((row.check_id, declared), 0) + 1
            if target == OFF:
                continue
            if target != declared:
                # Only set when the policy actually moved it: a populated
                # declared_severity means "this was re-mapped", and filling it
                # in unconditionally would make every row look overridden.
                row.declared_severity = declared
                row.severity = target
            kept.append(row)

        changes = [
            Change(check_id=cid, declared=declared, applied=resolved[cid], rows=n)
            for (cid, declared), n in sorted(counts.items())
            if resolved[cid] != declared
        ]
        return kept, changes


def _squash(check_id: str) -> str:
    """A check id with its dashes and zeros removed, for near-miss hints."""
    return check_id.replace("-", "").replace("0", "").upper()
