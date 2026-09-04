"""Small text-editing primitives shared by the TARQL/ontology repair
suggesters: unified diffs, and CURIE/IRI-aware find-and-replace over raw
query text (there is no full SPARQL-aware rewriter here -- these are
targeted, regex-based edits scoped to exactly the token the finding names,
same "don't rewrite what you don't need to touch" spirit as
``applyRepair.ts``'s append-only insert path).
"""
from __future__ import annotations

import difflib
import re
from typing import Dict, Optional, Tuple


def unified_diff(old_text: str, new_text: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=filename,
            tofile=filename,
        )
    )


def split_iri(iri: str) -> Tuple[str, str]:
    """(namespace, local_name), '#'-then-'/' convention, matching
    ``checks.registry``'s and ``versioning.rename_detection``'s own."""
    iri = str(iri)
    for sep in ("#", "/"):
        if sep in iri:
            head, _, tail = iri.rpartition(sep)
            return head + sep, tail
    return "", iri


def prefix_for_namespace(namespace: str, prefixes: Dict[str, str]) -> Optional[str]:
    for name, iri in prefixes.items():
        if iri == namespace:
            return name
    return None


def replace_prefix_iri(text: str, prefix: str, new_iri: str) -> str:
    """Rewrites the `<new_iri>` of the (first) `PREFIX prefix: <...>` line
    matching `prefix` exactly, leaving everything else -- including every
    other PREFIX line and the query body -- untouched."""
    pattern = re.compile(
        r"(^\s*PREFIX\s+" + re.escape(prefix) + r"\s*:\s*<)([^>]*)(>)",
        re.IGNORECASE | re.MULTILINE,
    )
    return pattern.sub(lambda m: f"{m.group(1)}{new_iri}{m.group(3)}", text, count=1)


def rename_prefix_label(text: str, old_prefix: str, new_prefix: str) -> str:
    """Renames every use of `old_prefix:` (the `PREFIX` declaration line
    itself, and every `old_prefix:localName` reference in the query body)
    to `new_prefix:`."""
    decl_pattern = re.compile(
        r"(^\s*PREFIX\s+)" + re.escape(old_prefix) + r"(\s*:\s*<[^>]*>)",
        re.IGNORECASE | re.MULTILINE,
    )
    text = decl_pattern.sub(lambda m: f"{m.group(1)}{new_prefix}{m.group(2)}", text, count=1)
    usage_pattern = re.compile(r"(?<![:\w])" + re.escape(old_prefix) + r":(?=\w)")
    return usage_pattern.sub(f"{new_prefix}:", text)


def render_term(iri: str, prefixes: Dict[str, str]) -> str:
    """CURIE form if `prefixes` (the target file's own PREFIX declarations)
    already binds `iri`'s namespace, else the safe bracketed-IRI form."""
    namespace, local = split_iri(iri)
    prefix = prefix_for_namespace(namespace, prefixes)
    return f"{prefix}:{local}" if prefix else f"<{iri}>"


def _sub_outside_comments(text: str, pattern: re.Pattern, replacement: str) -> str:
    """`pattern.sub` restricted to the parts of `text` that are not comments.

    A rename is a code edit; a comment is prose *about* code. Substituting
    into both turned a true remark into a false one: a comment reading
    *"every row is typed `acme:Engineer`, which v2.0.0 renames to
    `acme:SoftwareEngineer`"* became *"every row is typed
    `acme:SoftwareEngineer`, which v2.0.0 renames to
    `acme:SoftwareEngineer`"* -- harmless to the query, nonsense to the next
    reader, and worse than leaving it alone.

    String literals are deliberately still rewritten. In a TARQL query an IRI
    template is built out of literals -- `CONCAT("acme:_Engineer_", ?id)` --
    so a term rename usually *must* reach inside them. That is a judgement
    about this domain, not a general rule, and it is why this is not simply
    "substitute outside comments and strings".

    `strip_comments` blanks comments to spaces while preserving every other
    offset, so a match found in the mask has the same position in the
    original, and the original's text can be spliced around it.
    """
    from ..sketch.bind_analysis import strip_comments   # local: keeps this module's imports light

    masked = strip_comments(text)
    out, last = [], 0
    for match in pattern.finditer(masked):
        out.append(text[last:match.start()])
        out.append(replacement)
        last = match.end()
    out.append(text[last:])
    return "".join(out)


def replace_term(text: str, old_iri: str, new_iri: str, prefixes: Dict[str, str]) -> str:
    """Replaces every occurrence of `old_iri` in `text` -- whether written
    as `<old_iri>` or as a `prefix:localName` CURIE under any of `prefixes`
    that happens to bind `old_iri`'s namespace -- with the best available
    rendering of `new_iri` (a CURIE if `prefixes` already binds its
    namespace, otherwise a bracketed IRI).

    Comments are left alone; see `_sub_outside_comments` for why, and for why
    string literals are not."""
    new_term = render_term(new_iri, prefixes)
    namespace, local = split_iri(old_iri)

    patterns = [re.compile(r"<" + re.escape(old_iri) + r">")]
    for name, iri in prefixes.items():
        if iri == namespace:
            patterns.append(re.compile(r"(?<![:\w])" + re.escape(name) + r":" + re.escape(local) + r"\b"))

    for pattern in patterns:
        text = _sub_outside_comments(text, pattern, new_term)
    return text
