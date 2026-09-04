"""Convert TARQL-style SPARQL CONSTRUCT queries into a Turtle sketch of the
data graph they build.

Each query's CONSTRUCT template is a pattern of variables bound from CSV
columns; turning `?variable` into `:variable` sketches those variables as
entities/properties in a scratch `:` namespace, giving a rough Turtle view of
the graph shape a query produces. This module can do that for a single query
file, or aggregate an entire folder of query files into one consolidated
Turtle file representing the combined graph shape.
"""

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass

from .. import io_utils

PREFIX_LINE_PATTERN = re.compile(r"^\s*PREFIX\s+(\w*):\s*<([^>]*)>", re.IGNORECASE | re.MULTILINE)
CONSTRUCT_KEYWORD_PATTERN = re.compile(r"\bCONSTRUCT\b", re.IGNORECASE)
VARIABLE_PATTERN = re.compile(r"\?(\w+)")

DEFAULT_BASE = "https://tarqlviz.org/"
DEFAULT_QUERY_GLOBS = "*.sparql,*.rq,*.tarql,*.tq"
DEFAULT_NAMESPACE_PREDICATE = ":isRepresentedBy"
DEFAULT_NAMESPACE_CONFLICT_PREDICATE = ":hasAmbiguousPrefix"


def scratch_namespace(base: str = DEFAULT_BASE) -> str:
    """The full scratch-entity namespace `write_turtle` binds its default
    `:name` prefix to, when a query doesn't declare its own empty prefix
    (single source of truth -- `graph_quality.default_ignored_predicates`
    and `pattern_consistency`'s own scratch-entity filtering both call this
    rather than re-deriving it, so the three can never drift apart).

    `base` is used unchanged if it already ends in `/` or `#` -- either is a
    valid namespace-terminating separator, and a caller-supplied `base` that
    intentionally uses `#` (e.g. matching an existing convention) is
    respected as given. Otherwise `/` is appended, this suite's own default
    -- never left bare, which would silently run the base and the local name
    together into one malformed token (e.g. `https://tarqlviz.orgvehicle`).
    """
    return base if base.endswith(("/", "#")) else base + "/"


@dataclass
class QueryGraph:
    """The prefixes and construct-template triples extracted from one query file."""
    source: str
    prefixes: dict
    triples: str


def extract_prefixes(text):
    """Return an ordered {prefix_name: iri} dict of PREFIX declarations in the query."""
    prefixes = {}
    for name, iri in PREFIX_LINE_PATTERN.findall(text):
        prefixes[name] = iri
    return prefixes


def _find_matching_brace(text, open_index):
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("Unbalanced braces in CONSTRUCT clause")


def extract_construct_blocks(text):
    """Return the raw text inside every CONSTRUCT { ... } template in the query.

    Braces are counted rather than matched with a greedy regex, so this also
    copes with the `CONSTRUCT WHERE { ... }` shorthand and with nested braces
    (e.g. GRAPH blocks) inside the template.
    """
    blocks = []
    for kw_match in CONSTRUCT_KEYWORD_PATTERN.finditer(text):
        brace_start = text.find("{", kw_match.end())
        if brace_start == -1:
            continue
        brace_end = _find_matching_brace(text, brace_start)
        blocks.append(text[brace_start + 1:brace_end])
    return blocks


def variables_to_entities(construct_text):
    """Turn SPARQL variables in a CONSTRUCT template into turtle entities in the default `:` namespace."""
    return VARIABLE_PATTERN.sub(r":\1", construct_text)


def terminate_block(turtle_text):
    """Ensure one CONSTRUCT block's turtle ends in a statement terminator.

    SPARQL lets the last triple of a template omit its ``.`` before the
    closing brace, and lets a property list end on a dangling ``;``. Both are
    legal and neither is legal Turtle, so a block copied out verbatim
    produced a ``sketch.ttl`` that this suite then failed to parse -- a crash
    on a valid input file, in the stage whose whole job is reading valid
    input files.

    Terminating each *block* rather than the file is what makes this
    complete. ``parse_query`` joins blocks with a newline, so a file with two
    CONSTRUCTs where only the first is unterminated glues the second onto it;
    a terminator added at the end of the file would fix the single-block case
    and leave that one broken. Three shapes, three different parser messages,
    one cause:

      one block, no final ``.``        -> "EOF found after object"
      two blocks, first unterminated   -> "expected '.' or '}' or ']'"
      trailing ``;`` before ``}``      -> "objectList expected"

    None of them ever produced *wrong* triples -- Turtle rejects all three
    rather than splicing them into something valid -- so this was always a
    crash and never bad data. Worth knowing, since it is the first question
    anyone asks of a bug in a writer.

    Comments are why this is not a one-line ``rstrip``. A block may end with
    a trailing comment (this repo's own fixtures do), and appending " ." to
    that line puts the terminator inside the comment. The comment scanner
    from ``bind_analysis`` blanks comments while preserving offsets, so the
    last *meaningful* character can be found in the mask and acted on in the
    original.
    """
    from .bind_analysis import strip_comments   # local: keeps this module's imports light

    masked = strip_comments(turtle_text).rstrip()
    if not masked:
        return turtle_text.rstrip()
    last = len(masked) - 1
    char = masked[last]
    if char == ".":
        return turtle_text.rstrip()
    if char in ";,":
        # A dangling separator: the statement is complete, so make it say so
        # rather than appending a "." that would follow an empty object list.
        return (turtle_text[:last] + "." + turtle_text[last + 1:]).rstrip()
    # On its own line, so a trailing comment cannot swallow it.
    return turtle_text.rstrip() + "\n."


def parse_query(path):
    """Read one query file (a local path, an http(s) URL, or either
    gzip-compressed) and pull out its prefixes and turtle-ified CONSTRUCT
    triples."""
    text = io_utils.read_text(path)
    prefixes = extract_prefixes(text)
    blocks = extract_construct_blocks(text)
    triples = "\n".join(
        terminate_block(variables_to_entities(block).strip())
        for block in blocks if block.strip()
    )
    return QueryGraph(source=path, prefixes=prefixes, triples=triples)


def namespace_legend_triples(prefixes, predicate=DEFAULT_NAMESPACE_PREDICATE):
    """Describe each namespace's prefix abbreviation as a data triple, e.g.

    <http://xmlns.com/foaf/0.1/> :isRepresentedBy "foaf" .

    so the abbreviation-to-namespace mapping is visible in the graph itself,
    not just in the (often visualiser-invisible) @prefix directives.
    """
    return "\n".join(f'<{iri}> {predicate} "{name}" .' for name, iri in prefixes.items())


def namespace_conflict_triples(namespace_names, predicate=DEFAULT_NAMESPACE_CONFLICT_PREDICATE):
    """Flag namespaces that were abbreviated with more than one distinct prefix, e.g.

    <https://example.org/> :hasAmbiguousPrefix true .

    `namespace_names` maps namespace IRI -> {prefix_name: source_file}, as
    built while merging prefixes across queries. Multiple :isRepresentedBy
    triples for the same namespace already show this in the graph itself
    (one node fanning out to several abbreviation literals); this adds an
    explicit, queryable/filterable marker on top of that for visualisers.
    """
    return "\n".join(
        f"<{iri}> {predicate} true ."
        for iri, names in namespace_names.items()
        if len(names) > 1
    )


def write_turtle(
    graphs,
    path_out,
    base=DEFAULT_BASE,
    namespace_predicate=DEFAULT_NAMESPACE_PREDICATE,
    namespace_conflict_predicate=DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
    include_namespace_legend=True,
):
    """Write the prefixes and CONSTRUCT triples of one or more parsed queries as a single turtle file."""
    merged_prefixes = {}
    namespace_names = {}  # iri -> {prefix_name: first source file that used it}
    for graph in graphs:
        for name, iri in graph.prefixes.items():
            existing = merged_prefixes.get(name)
            if existing is not None and existing != iri:
                print(
                    f"warning: prefix '{name}:' redefined in {graph.source} "
                    f"({existing} -> {iri}); keeping first definition",
                    file=sys.stderr,
                )
            else:
                merged_prefixes[name] = iri
            namespace_names.setdefault(iri, {}).setdefault(name, graph.source)

    for iri, names in namespace_names.items():
        if len(names) > 1:
            variants = ", ".join(f"'{name}:' (from {os.path.basename(source)})" for name, source in names.items())
            print(
                f"warning: namespace <{iri}> is abbreviated inconsistently across files: {variants}",
                file=sys.stderr,
            )

    with open(path_out, "w", encoding="utf-8") as out:
        for name, iri in merged_prefixes.items():
            print(f"@prefix {name}: <{iri}> .", file=out)
        print(f"@base <{base}> .", file=out)
        if "" not in merged_prefixes:
            print(f"@prefix : <{scratch_namespace(base)}> .", file=out)
        print(file=out)
        if include_namespace_legend and merged_prefixes:
            print("# namespace legend", file=out)
            print(namespace_legend_triples(merged_prefixes, namespace_predicate), file=out)
            conflicts = namespace_conflict_triples(namespace_names, namespace_conflict_predicate)
            if conflicts:
                print(conflicts, file=out)
            print(file=out)
        for graph in graphs:
            if not graph.triples:
                continue
            print(f"# source: {os.path.basename(graph.source)}", file=out)
            print(graph.triples, file=out)
            print(file=out)


def visualise_tarql(path_in, path_out, **write_turtle_kwargs):
    """Convert a single query file to a turtle file."""
    write_turtle([parse_query(path_in)], path_out, **write_turtle_kwargs)


def visualise_folder(folder_in, path_out, patterns=DEFAULT_QUERY_GLOBS, **write_turtle_kwargs):
    """Aggregate every query file in a folder into one consolidated turtle file.

    `patterns` is a comma-separated list of glob patterns (relative to
    `folder_in`) used to find query files, e.g. "*.sparql,*.rq".
    """
    if os.path.isfile(folder_in):
        # A single query file. This module's own description has always said
        # "for a single query file or a whole folder of them", and the flag
        # reads that way too -- but the glob was joined onto the argument
        # unconditionally, so a file produced "No files matching '*.rq' found
        # in employees.rq", which names the file it was given and says it
        # cannot find it. The pattern is not applied here: naming a file is
        # already the narrowest possible selection, and filtering it out
        # again would be the same confusing silence in a different form.
        paths = [folder_in]
    else:
        paths = sorted(
            {
                p
                for pattern in patterns.split(",")
                for p in glob.glob(os.path.join(folder_in, pattern.strip()))
            }
        )
    if not paths:
        hint = "" if os.path.isdir(folder_in) else f" ({folder_in} is not a directory)"
        raise FileNotFoundError(
            f"No files matching '{patterns}' found in {folder_in}{hint}"
        )
    graphs = [parse_query(p) for p in paths]
    write_turtle(graphs, path_out, **write_turtle_kwargs)
    return paths


def main(argv):
    parser = argparse.ArgumentParser(
        prog="tarql-visualiser",
        description='Converts TARQL "CONSTRUCT" query templates to a Turtle sketch of the data '
                    "graph they build, for a single query file or a whole folder of them.",
        epilog="version 0.2",
    )
    parser.add_argument("-i","--input", help="a query file, or a folder of query files")
    parser.add_argument("-o", "--output", default=None, help="output .ttl file (default: <input>.ttl)")
    parser.add_argument(
        "-p",
        "--file-pattern",
        default=DEFAULT_QUERY_GLOBS,
        help=f"comma-separated glob pattern(s) used to find query files when input is a folder "
             f"(default: {DEFAULT_QUERY_GLOBS})",
    )
    parser.add_argument(
        "--namespace-predicate",
        default=DEFAULT_NAMESPACE_PREDICATE,
        help=f"predicate used for the <namespace> {{predicate}} \"prefix\" legend triples "
             f"(default: {DEFAULT_NAMESPACE_PREDICATE})",
    )
    parser.add_argument(
        "--namespace-conflict-predicate",
        default=DEFAULT_NAMESPACE_CONFLICT_PREDICATE,
        help=f"predicate used to flag a namespace that is abbreviated inconsistently across files "
             f"(default: {DEFAULT_NAMESPACE_CONFLICT_PREDICATE})",
    )
    parser.add_argument(
        "--no-namespace-legend",
        action="store_true",
        help="omit the namespace legend and prefix-conflict triples from the output",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="print which query files --file-pattern actually matched, when input is a folder")
    args = parser.parse_args(argv)
    write_turtle_kwargs = {
        "namespace_predicate": args.namespace_predicate,
        "namespace_conflict_predicate": args.namespace_conflict_predicate,
        "include_namespace_legend": not args.no_namespace_legend,
    }

    if os.path.isdir(args.input):
        output = args.output or os.path.normpath(args.input) + ".ttl"
        paths = visualise_folder(args.input, output, args.file_pattern, **write_turtle_kwargs)
        if args.verbose:
            print(f"[verbose] {args.input} (--file-pattern {args.file_pattern}): {len(paths)} query file(s) matched:")
            for p in paths:
                print(f"    {p}")
        noun = "query" if len(paths) == 1 else "queries"
        print(f"Consolidated {len(paths)} {noun} into {output}")
    else:
        output = args.output or args.input + ".ttl"
        visualise_tarql(args.input, output, **write_turtle_kwargs)
        print(f"Wrote {output}")


def run_tool():
    main(sys.argv[1:] if len(sys.argv) > 1 else ["-h"])


if __name__ == "__main__":
    run_tool()
