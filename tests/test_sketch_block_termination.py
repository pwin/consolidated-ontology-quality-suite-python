"""A CONSTRUCT whose last triple omits its `.` is legal SPARQL, and used to
crash the sketch stage.

SPARQL lets the final triple of a template drop the `.` before the closing
brace, and lets a property list end on a dangling `;`. Both are legal, and
neither is legal Turtle. `tarql_visualiser` copies the block out verbatim, so
it wrote a `sketch.ttl` it then could not parse -- an unhandled ValueError, on
a valid input file, in the stage whose entire job is reading valid input
files.

Three shapes, three different parser messages, one cause:

    one block, no final `.`         BadSyntax: EOF found after object
    two blocks, first unterminated  BadSyntax: expected '.' or '}' or ']'
    trailing `;` before `}`         BadSyntax: objectList expected

The variety matters more than it looks: anyone who met this once and searched
for "EOF found after object" would conclude the other two were different bugs.

It never produced *wrong* triples. Turtle rejects all three rather than
splicing them into something valid, so this was always a crash and never bad
data -- checked deliberately, because a writer that emits subtly wrong output
is far worse than one that fails, and that is the first question to ask.

Why no fixture ever caught it: every TARQL file in this repo, and every one
in the worked examples, happens to write the dot. It is the ordinary way to
write the template. Two of those fixtures were added while building the TQL
checks and neither tripped it.
"""
import pytest
import rdflib

from ontology_suite import pipeline
from ontology_suite.sketch import tarql_visualiser

PREAMBLE = (
    "prefix ex: <https://example.org/>\n"
    "prefix exd: <https://example.org/data/>\n"
    "prefix tarql: <http://tarql.github.io/tarql#>\n"
)
WHERE = 'WHERE { BIND(tarql:expandPrefixedName(CONCAT("exd:_A_", ?id)) AS ?a_IRI) }\n'
WHERE_B = 'WHERE { BIND(tarql:expandPrefixedName(CONCAT("exd:_B_", ?id)) AS ?b_IRI) }\n'

# Each of these is legal SPARQL, and each used to crash the stage.
SHAPES = {
    "no final dot": PREAMBLE + "CONSTRUCT {\n  ?a_IRI a ex:Alpha\n}\n" + WHERE,
    "trailing semicolon": PREAMBLE + "CONSTRUCT {\n  ?a_IRI a ex:Alpha ;\n}\n" + WHERE,
    "two blocks, first unterminated": (
        PREAMBLE
        + "CONSTRUCT {\n  ?a_IRI a ex:Alpha\n}\n" + WHERE
        + "CONSTRUCT {\n  ?b_IRI a ex:Beta .\n}\n" + WHERE_B
    ),
    # The ordinary form, which always worked. Here so a fix that broke it
    # would be caught by this module rather than somewhere unrelated.
    "terminated normally": PREAMBLE + "CONSTRUCT {\n  ?a_IRI a ex:Alpha .\n}\n" + WHERE,
    # rstrip() alone is wrong here: appending " ." to the last line would put
    # the terminator inside the comment. This repo's own fixtures end with
    # trailing comments, so this is not a hypothetical shape.
    "trailing comment after the last triple": (
        PREAMBLE + "CONSTRUCT {\n  ?a_IRI a ex:Alpha  # a note\n}\n" + WHERE
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_sketch_stage_reads_every_legal_template(shape, tmp_path):
    queries = tmp_path / "q"
    queries.mkdir()
    (queries / "q.rq").write_text(SHAPES[shape], encoding="utf-8")

    stage = pipeline.run_sketch_stage(str(queries), tmp_path / "out", query_pattern="*.rq")

    sketch = stage.artifacts["sketch_path"]
    graph = rdflib.Graph().parse(sketch, format="turtle")
    subjects = {
        str(s).rsplit("/", 1)[-1]
        for s, p, _ in graph
        if "isRepresentedBy" not in str(p) and "hasAmbiguousPrefix" not in str(p)
    }
    assert "a_IRI" in subjects, f"{shape}: the template's subject never reached the graph"


def test_two_blocks_stay_two_statements(tmp_path):
    """The reason each block is terminated rather than the file.

    `parse_query` joins blocks with a newline. Terminating only at the end of
    the file would leave the second block glued to the first -- which Turtle
    rejects, so it crashed rather than mis-parsing, but the fix has to
    separate them either way.
    """
    queries = tmp_path / "q"
    queries.mkdir()
    (queries / "q.rq").write_text(SHAPES["two blocks, first unterminated"], encoding="utf-8")

    stage = pipeline.run_sketch_stage(str(queries), tmp_path / "out", query_pattern="*.rq")
    graph = rdflib.Graph().parse(stage.artifacts["sketch_path"], format="turtle")

    typed = {
        str(s).rsplit("/", 1)[-1]: str(o).rsplit("/", 1)[-1]
        for s, p, o in graph.triples((None, rdflib.RDF.type, None))
    }
    assert typed == {"a_IRI": "Alpha", "b_IRI": "Beta"}, (
        "each block must keep its own subject and type"
    )


# ---------------------------------------------------------------------------
# terminate_block itself
# ---------------------------------------------------------------------------
def test_an_already_terminated_block_is_untouched():
    assert tarql_visualiser.terminate_block(":a a ex:Alpha .") == ":a a ex:Alpha ."


def test_a_missing_terminator_is_added_on_its_own_line():
    """On its own line so a trailing comment cannot swallow it."""
    assert tarql_visualiser.terminate_block(":a a ex:Alpha") == ":a a ex:Alpha\n."
    assert tarql_visualiser.terminate_block(":a a ex:Alpha # note") == ":a a ex:Alpha # note\n."


def test_a_dangling_separator_becomes_a_terminator():
    """Appending "." after a `;` would follow it with an empty object list,
    which is its own syntax error -- the separator has to be replaced."""
    assert tarql_visualiser.terminate_block(":a a ex:Alpha ;") == ":a a ex:Alpha ."
    assert tarql_visualiser.terminate_block(":a a ex:Alpha ,") == ":a a ex:Alpha ."


def test_a_dot_inside_a_literal_or_comment_does_not_count_as_a_terminator():
    """The reason the comment scanner is reused rather than a `rstrip()`
    written here: both of these end in a `.` that terminates nothing."""
    assert tarql_visualiser.terminate_block(':a ex:label "ends in a dot."').endswith('"\n.')
    assert tarql_visualiser.terminate_block(":a a ex:Alpha # see rule 1.") == (
        ":a a ex:Alpha # see rule 1.\n."
    )


def test_an_empty_block_is_not_given_a_stray_terminator():
    assert tarql_visualiser.terminate_block("") == ""
    assert tarql_visualiser.terminate_block("   # only a comment\n").endswith("comment")
