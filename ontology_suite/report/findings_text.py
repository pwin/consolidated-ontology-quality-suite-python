"""The findings as a person reads them: what, where, and the source line.

Every other surface this package writes is addressed to something other than a
reader at a terminal. ``full_results.csv`` is the complete machine record,
``cucumber.json`` is for a CI plugin, ``report.html`` is for a browser. None of
them answers the first question anyone has, which is *where is it and what does
it look like*.

Grouped by what the finding actually has. A finding that could be placed in a
file is grouped **by place**, so each source line is quoted once however many
checks fired on it -- measured on this repo's own worked example, 15 of 25
located findings shared a line with another, so a check-first layout quoted the
same three lines of Turtle over and over. A finding that could not be placed is
grouped **by check**, because the place is the one thing it hasn't got.

Remediation is stated once per check, at the end, under "how to fix". It is the
same sentence for every finding of a check, and on a run where one check fires
341 times, repeating it is most of the output.

Nothing is capped or sampled. It is the same finding set as
``full_results.csv``, differently arranged -- if it disagreed on the count it
would be worse than useless, because it looks like a summary and a summary that
quietly drops rows is the one thing a report must never be.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..checks.merge import ResultRow
from ..checks.registry import Registry

_CHECK_ID = re.compile(r"[A-Z][A-Z0-9]*-\d+")

CONTEXT_LINES = 2
SEVERITY_ORDER = {"Violation": 0, "Warning": 1, "Info": 2}


def _read_lines(path: str) -> Optional[List[str]]:
    """``None`` if the file cannot be read now. An extract is a courtesy, and
    losing it must not lose the finding -- the position is already known and
    gets printed either way."""
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _clusters(marked: Sequence[int], total: int, context: int = CONTEXT_LINES) -> List[Tuple[int, int]]:
    """Merge the context windows around ``marked`` into non-overlapping
    ``(first, last)`` ranges.

    Without this, two findings three lines apart quote overlapping extracts and
    the reader sees the same Turtle twice with a different line marked -- the
    per-check duplication moved rather than removed. Adjacent windows that
    touch are merged into one block with both lines marked, which is what a
    compiler does and what reads correctly when a class and its property are
    flagged together.
    """
    ranges: List[Tuple[int, int]] = []
    for line in sorted(set(marked)):
        first, last = max(1, line - context), min(total, line + context)
        if ranges and first <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], last))
        else:
            ranges.append((first, last))
    return ranges


def _extract(lines: List[str], first: int, last: int, marked: set, width: int) -> List[str]:
    out = []
    for number in range(first, last + 1):
        marker = ">" if number in marked else " "
        out.append(f"  {marker} {number:>{width}} | {lines[number - 1]}")
    return out


def _severity_rank(rows: Sequence[ResultRow]) -> int:
    return min(SEVERITY_ORDER.get(r.severity, 9) for r in rows)


def _distinguishers(row: ResultRow) -> str:
    """The fields that tell this finding apart from another of the same check
    at the same place -- and only when the message has not said them already.

    Both halves of that matter. Without it, STR-002 on this repo's worked
    example printed `http://www.w3.org/ns/org#` five times, identically: five
    genuinely different findings whose distinguishing field was `path` (the
    undeclared predicate -- dcterms:contributor, created, license, modified,
    title), which the check's own message does not name. Five identical lines
    that are not duplicates is worse than duplication, because the reader
    cannot tell which it is.

    Suppressed when the value already appears in the message, and -- by
    `_disambiguated`, which is the only caller -- suppressed entirely unless
    two lines in the same group would otherwise come out identical. Shown
    unconditionally it is mostly noise: a located TARQL finding would carry
    `(path .../tarql/expression, value CONCAT("exd:_T_", ?id))`, an internal
    predicate the reader did not ask about and an expression the quoted source
    line is already showing.
    """
    extras = [
        f"{label} {value}"
        for label, value in (("path", row.path), ("value", row.value))
        if value and value != row.focus_node and value not in row.message
    ]
    return "  (" + ", ".join(extras) + ")" if extras else ""


def _finding_line(row: ResultRow, width: int) -> str:
    """One finding, one line: its line number, check id, severity, and the
    check's own words.

    The line number is repeated here even though the extract above already
    marks it, because a cluster can mark several lines at once and many check
    messages are generic -- four classes flagged in one block produced four
    identical "Term must have at least one skos:prefLabel" lines with nothing
    to tell them apart. The number is what ties each back to its subject.
    """
    label = row.check_id or "UNMAPPED"
    number = f"{row.line:>{width}}" if row.line else ""
    return f"      {number}  {label:<9} {row.severity:<9} {row.message}"


def _disambiguated(rows: Sequence[ResultRow], base) -> List[str]:
    """Render ``rows`` with ``base``, adding distinguishing fields only if two
    of them would otherwise be the same text.

    The cheapest rule that is both unambiguous and quiet. Identical lines are
    only acceptable when the findings really are indistinguishable, and they
    never are -- the dedup key guarantees each row differs in check, focus
    node, path or value, so a collision here means the difference is in a
    field the line is not showing.
    """
    out = [base(row) for row in rows]
    if len(set(out)) == len(out):
        return out
    return [text + _distinguishers(row) for text, row in zip(out, rows)]


def _located(rows: Sequence[ResultRow]) -> List[str]:
    out: List[str] = []
    by_file: Dict[str, List[ResultRow]] = defaultdict(list)
    for row in rows:
        by_file[row.source_file].append(row)

    for source_file in sorted(by_file, key=lambda f: (_severity_rank(by_file[f]), f)):
        file_rows = by_file[source_file]
        out.append(source_file)
        out.append("")

        lines = _read_lines(source_file)
        by_line: Dict[int, List[ResultRow]] = defaultdict(list)
        for row in file_rows:
            by_line[row.line].append(row)

        if lines is None:
            # The file moved between the run and the render. Positions are
            # still real and still worth printing; only the quotation is lost.
            out.append("  (source no longer readable -- positions only)")
            out.append("")
            for line in sorted(by_line):
                out.append(f"  > {line}")
                out.extend(_disambiguated(
                    sorted(by_line[line], key=_sort_key), lambda r: _finding_line(r, 0)
                ))
                out.append("")
            continue

        width = len(str(max(by_line) + CONTEXT_LINES))
        for first, last in _clusters(list(by_line), len(lines)):
            marked = {n for n in by_line if first <= n <= last}
            out.extend(_extract(lines, first, last, marked, width))
            out.append("")
            block_rows = [
                row
                for number in sorted(marked)
                for row in sorted(by_line[number], key=_sort_key)
            ]
            out.extend(_disambiguated(block_rows, lambda r: _finding_line(r, width)))
            out.append("")
    return out


def _message_template(row: ResultRow) -> str:
    """The message with the focus node -- and only the focus node -- blanked.

    Most check messages are templates filled with the focus node: "<X> has no
    rdfs:label". Printed under every finding of a check that is one sentence
    repeated hundreds of times with one word changing, and that word is on the
    line beneath it. Blanking it leaves the sentence, and if every row in a
    group reduces to the same sentence it is a template and belongs in the
    heading, said once.

    Only the focus node, because it is the only field this layout guarantees
    to show on the row underneath. Blanking `path` and `value` too collapsed
    STR-005 -- "Property {} declares rdfs:domain {} which is never given an
    rdf:type" -- into a single shared template whose second blank was then
    printed nowhere, so the reader could see that *a* domain value was untyped
    but not which. Leaving those fields in means such messages no longer
    reduce alike, so they are printed per row, which is the correct answer:
    they carry something the row does not.
    """
    return row.message.replace(row.focus_node, "{}") if row.focus_node else row.message


def _unlocated(rows: Sequence[ResultRow], registry: Optional[Registry]) -> List[str]:
    out: List[str] = []
    by_check: Dict[Optional[str], List[ResultRow]] = defaultdict(list)
    for row in rows:
        by_check[row.check_id].append(row)

    for check_id in sorted(by_check, key=lambda c: (_severity_rank(by_check[c]), c or "ZZZ")):
        group = by_check[check_id]
        check = registry.get(check_id) if (registry and check_id) else None
        title = (check.title if check else group[0].title) or ""
        label = check_id or "UNMAPPED"
        out.append(f"  {label}  {group[0].severity}  {title}  ({len(group)})".rstrip())

        shared = len({_message_template(r) for r in group}) == 1
        if shared:
            out.append(f"      {_message_template(group[0])}")
            base = lambda r: f"        {r.focus_node}"          # noqa: E731
        else:
            base = lambda r: f"      {r.focus_node}  {r.message}"   # noqa: E731
        out.extend(_disambiguated(sorted(group, key=_sort_key), base))
        out.append("")
    return out


def _fixes(rows: Sequence[ResultRow], registry: Optional[Registry]) -> List[str]:
    """One entry per check that fired -- the remediation, said once.

    Doubles as the check-centric index the by-place layout above does not
    give: every check id in the report appears here exactly once, with its
    title and its count.
    """
    by_check: Dict[Optional[str], List[ResultRow]] = defaultdict(list)
    for row in rows:
        by_check[row.check_id].append(row)

    out: List[str] = []
    for check_id in sorted(by_check, key=lambda c: (_severity_rank(by_check[c]), c or "ZZZ")):
        group = by_check[check_id]
        check = registry.get(check_id) if (registry and check_id) else None
        title = (check.title if check else group[0].title) or ""
        remediation = (check.remediation if check else None) or group[0].remediation
        label = check_id or "UNMAPPED"
        out.append(f"  {label}  {group[0].severity}  {title}  ({len(group)})".rstrip())
        if remediation:
            out.append(f"      {remediation}")
        out.append("")
    return out


def _sort_key(row: ResultRow):
    return (
        SEVERITY_ORDER.get(row.severity, 9),
        row.check_id or "ZZZ",
        row.focus_node,
    )


def _rule(text: str) -> str:
    return f"-- {text} " + "-" * max(3, 72 - len(text))


def load_themes(path: str | Path) -> Dict[str, List[str]]:
    """A ``{theme: [check_id, ...]}`` map, from JSON or a two-column CSV.

    A *theme* is a question in the reader's own words -- "IRI construction
    pattern not updated following a model change" -- rather than a check id.
    The suite cannot supply these: the grouping depends on what a project is
    asking of its ontology, it is many-to-many (one survey of this suite maps
    38 questions onto 45 checks across 48 pairs), and several questions are
    answered by things with no registry id at all. So it is an input, loaded
    through the same extension points as `--registry` and `--sparql`, and a
    project that has such a survey gets its own organisation of the findings
    without either repo depending on the other.

    JSON:  {"theme": ["STR-002", "TQL-001"], ...}
    CSV:   check_id,theme   (header optional; first column is the check id)
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    themes: Dict[str, List[str]] = defaultdict(list)

    if path.suffix.lower() == ".json":
        for theme, ids in json.loads(text).items():
            themes[theme].extend(ids)
    else:
        for record in csv.reader(io.StringIO(text)):
            if len(record) < 2:
                continue
            check_id, theme = record[0].strip(), record[1].strip()
            # Tolerates a header row without needing to be told about one: a
            # check id is the shape `ABC-123`, and "check_id" is not.
            if check_id and theme and _CHECK_ID.fullmatch(check_id):
                themes[theme].append(check_id)
    return dict(themes)


def _themes_index(rows: Sequence[ResultRow], themes: Dict[str, List[str]]) -> List[str]:
    """Which questions this run has something to say about.

    An index, deliberately, not a fourth listing of the findings. Every
    finding is already printed once under its place or its check; repeating
    the bodies here to give a third view would be exactly the restatement the
    rest of this file is arranged to avoid. What a reader gains is the
    triage order -- which question to look at first -- and the check ids to
    grep for.
    """
    by_check: Dict[Optional[str], List[ResultRow]] = defaultdict(list)
    for row in rows:
        by_check[row.check_id].append(row)

    theme_of: Dict[str, List[str]] = defaultdict(list)
    for theme, ids in themes.items():
        for check_id in ids:
            theme_of[check_id].append(theme)

    counted: Dict[str, List[Tuple[str, int]]] = {}
    for theme, ids in themes.items():
        members = [
            (check_id, len(by_check[check_id]))
            for check_id in sorted(set(ids))
            if by_check.get(check_id)
        ]
        if members:
            counted[theme] = members

    out: List[str] = []
    # The mapping is many-to-many by nature -- one check can be evidence for
    # several questions, and QUA-009 answering both "missing preferred label"
    # and "multiple preferred labels for the same language" is not a mistake
    # in the survey. Said out loud, because a reader who adds the counts up
    # and does not get the total at the top will assume one of them is wrong.
    if any(len(t) > 1 for t in theme_of.values()):
        out.append("  A check can be evidence for more than one question, so these counts")
        out.append("  overlap and do not sum to the total above.")
        out.append("")

    # Most findings first: this is a triage order, and a question with 300
    # findings against it is where to start whatever it is called.
    for theme, members in sorted(
        counted.items(), key=lambda item: (-sum(n for _, n in item[1]), item[0])
    ):
        out.append(f"  {theme}  ({sum(n for _, n in members)})")
        out.append("      " + ", ".join(f"{check_id} x{n}" for check_id, n in members))
        out.append("")

    # Stated rather than left out. A reader who cannot find their check here
    # must be able to tell "it found nothing" from "this survey asks no
    # question about it", and only the first of those means there is nothing
    # to do.
    unmapped = sorted(
        {check_id for check_id in by_check if check_id not in theme_of},
        key=lambda c: c or "",
    )
    if unmapped:
        total = sum(len(by_check[check_id]) for check_id in unmapped)
        out.append(f"  not mapped to a question  ({total})")
        out.append("      " + ", ".join(check_id or "UNMAPPED" for check_id in unmapped))
        out.append("")
    return out


def render(
    rows: Sequence[ResultRow],
    registry: Optional[Registry] = None,
    themes: Optional[Dict[str, List[str]]] = None,
) -> str:
    if not rows:
        return "No findings.\n"

    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.severity] += 1
    summary = ", ".join(
        f"{counts[sev]} {sev}" for sev in ("Violation", "Warning", "Info") if counts[sev]
    )

    placed = [r for r in rows if r.source_file and r.line]
    unplaced = [r for r in rows if not (r.source_file and r.line)]
    places = len({(r.source_file, r.line) for r in placed})

    out: List[str] = [f"{len(rows)} finding(s): {summary}", ""]

    if themes:
        out.append(_rule("by question"))
        out.append("")
        out.extend(_themes_index(rows, themes))

    if placed:
        out.append(_rule(f"located: {len(placed)} finding(s) at {places} place(s)"))
        out.append("")
        out.extend(_located(placed))

    if unplaced:
        out.append(_rule(f"not located: {len(unplaced)} finding(s)"))
        out.append("")
        # Said plainly rather than left to be inferred from a missing column.
        # On this repo's own worked example every one of these was an imported
        # FOAF, Dublin Core or W3C term, and a reader who does not know that
        # concludes the locator is broken rather than that the findings are
        # about somebody else's vocabulary. That is also the cue to reach for
        # --own-namespace.
        out.append("  No declaring line was found in the file(s) checked. The usual reason is")
        out.append("  that the term belongs to an imported vocabulary rather than to this")
        out.append("  ontology -- if that is so for most of these, --own-namespace will")
        out.append("  restrict the run to your own terms. Blank nodes can never be placed.")
        out.append("")
        out.extend(_unlocated(unplaced, registry))

    out.append(_rule("how to fix"))
    out.append("")
    out.extend(_fixes(rows, registry))

    return "\n".join(out).rstrip() + "\n"


def write_findings_text(
    rows: Sequence[ResultRow],
    registry: Optional[Registry],
    out_path: str | Path,
    themes: Optional[Dict[str, List[str]]] = None,
) -> str:
    text = render(rows, registry, themes)
    Path(out_path).write_text(text, encoding="utf-8")
    return text
