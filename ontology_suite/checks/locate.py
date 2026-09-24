"""Best-effort source positions for findings that have none.

A finding is graph-shaped. ``ResultRow`` names an IRI, because that is all a
check can know: rdflib discards source position at parse time, so by the time
any check runs, "which line" is no longer a question the data can answer. That
is fine for a machine and poor for a person, who has to grep the IRI out of the
file to act on it.

This recovers the position by going back to the text. It looks for the line
that *declares* the focus node -- the conventional Turtle subject-block form,
where the subject starts a line -- and answers ``None`` when it cannot find
one.

``None`` is the point of the module. The obvious implementation returns line 1
when it fails, which reads as a real answer and sends the reader to the top of
the file; the VS Code extension's equivalent does exactly that (falling back to
``new vscode.Range(0, 0, 0, 0)``) and has to spell the IRI into the message to
compensate. A confidently wrong line number is worse than an absent one, for
the same reason an unsubstituted ``{$maxCount}`` is better than the word
"None": the reader can act on "I don't know", and cannot act on a lie.

So this declines in every case it cannot settle:

* a blank node -- ``_:b0`` is minted fresh on each parse and names nothing in
  the text;
* a term the file never declares, which is the *normal* case for a finding
  about an imported class;
* a term written only inline, as an object or inside a bracketed blank node,
  never as a subject at the start of a line.

Prefixes come from the text itself rather than from the parsed graph's
namespace manager. The CURIE being searched for is the one *written in this
file*, and a graph merged from imports will have several prefixes bound to one
namespace -- the aliasing that made ``docgen``'s external-term resolution fail
silently for months, where W3C's org.ttl binds ``http://www.w3.org/ns/org#``
as both ``org:`` and the default ``:``, and inverting the map let the later
declaration win. Reading this file's own declarations cannot go wrong that way.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..sketch.bind_analysis import strip_comments

# `@prefix ex: <...> .` (Turtle) and `PREFIX ex: <...>` (SPARQL/Turtle 1.2),
# including the empty prefix. The trailing `.` is optional here because the
# SPARQL form has none and this only needs the binding.
_PREFIX_DECL = re.compile(
    r"^\s*(?:@prefix|PREFIX)\s+([A-Za-z][\w.-]*)?:\s*<([^>]*)>",
    re.IGNORECASE | re.MULTILINE,
)


def prefixes_in_text(text: str) -> Dict[str, str]:
    """``{prefix: namespace}`` as *this text* declares them.

    Comments are masked first, so a commented-out `@prefix` line does not
    contribute a binding that no term in the file actually uses.
    """
    return {
        (match.group(1) or ""): match.group(2)
        for match in _PREFIX_DECL.finditer(strip_comments(text))
    }


def curies_for(iri: str, prefixes: Dict[str, str]) -> List[str]:
    """Every CURIE this prefix table can write ``iri`` as, longest namespace
    first.

    Plural deliberately. One namespace is often bound under several prefixes,
    and the file may use any of them; trying all is cheap and trying one is a
    coin toss. Longest first so a term under both `http://x/ns/` and
    `http://x/` is written against the more specific binding, which is what a
    person would have written.
    """
    out: List[Tuple[int, str]] = []
    for prefix, namespace in prefixes.items():
        if namespace and iri.startswith(namespace):
            local = iri[len(namespace):]
            # A local name that still contains a separator means the binding
            # was a prefix of the IRI but not its namespace.
            if local and not any(ch in local for ch in "/#"):
                out.append((len(namespace), f"{prefix}:{local}"))
    return [curie for _, curie in sorted(out, key=lambda pair: -pair[0])]


def find_declaring_line(text: str, iri: str, prefixes: Optional[Dict[str, str]] = None) -> Optional[int]:
    """The 1-based line that declares ``iri`` as a subject, or ``None``.

    "Declares" means the conventional Turtle layout -- the subject term at the
    start of a line, with its predicates following on that line or indented
    under it. That is how every ontology in this repo and every one these
    checks have been run against is written, and it is what makes a one-pass
    text scan enough.

    Comments are masked before the scan, so a remark naming the term is never
    mistaken for its declaration. That matters more than it sounds: a term is
    frequently *discussed* in a comment right above where it is defined, and
    matching the remark would report the line above the real one -- an
    off-by-one that looks plausible enough to go unnoticed.
    """
    if not iri or iri.startswith("_:"):
        return None
    if prefixes is None:
        prefixes = prefixes_in_text(text)

    candidates = curies_for(iri, prefixes) + [f"<{iri}>"]
    for number, line in enumerate(strip_comments(text).splitlines(), start=1):
        stripped = line.lstrip()
        for candidate in candidates:
            if stripped == candidate or stripped.startswith(candidate + " "):
                return number
    return None


def locate_rows(rows: Iterable, source_path: str | Path) -> int:
    """Fill in ``source_file``/``line`` on every row that can be placed in
    ``source_path``, and return how many were placed.

    Rows that already carry a position are left alone: the TARQL checks get
    theirs from the query parser, which knows exactly where the BIND was and
    does not need guessing at.

    The count is returned so the caller can say what fraction of the findings
    it could place. A run that placed 3 of 200 is telling the reader something
    -- usually that the findings are about imported terms -- and silence there
    would just look like the feature was not working.
    """
    path = Path(source_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    prefixes = prefixes_in_text(text)
    placed = 0
    for row in rows:
        if getattr(row, "line", None) is not None:
            continue
        line = find_declaring_line(text, row.focus_node, prefixes)
        if line is not None:
            row.source_file = str(path)
            row.line = line
            placed += 1
    return placed
