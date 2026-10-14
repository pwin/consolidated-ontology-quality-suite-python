"""Each query file's triples are read with that file's own prefixes.

`write_turtle` merges the prefix declarations of every query into one block at
the top of `sketch.ttl` and then copies each file's CONSTRUCT triples out
verbatim underneath. The merge keeps the first definition when two files
disagree, so every CURIE in the document resolved against whichever file
happened to be read first.

It cut both ways, and silently either way. With the odd file sorting first,
the *correct* files' triples came out under the wrong namespace -- and the
findings that followed named those files, not the one at fault. With it
sorting last, its own mistake was quietly corrected and never reported at
all. Renaming a file was enough to swap which, with no mapping changed.

The fix is ordinary Turtle rather than a trick: a prefix may be re-declared at
the top level at any point, and the later declaration governs what follows.
Each file's block now re-declares its own prefixes ahead of its own triples.

What these tests pin is **order independence**: the same set of files must
produce the same triples whatever they are called. That is the property the
defect broke, and it is stronger than asserting any particular namespace --
a fix that resolved everything to the wrong namespace consistently would pass
an "is it the first file's" test and fail this one.
"""
import pytest
from rdflib import Graph

from ontology_suite.sketch import tarql_visualiser

GIST_SLASH = "https://w3id.org/semanticarts/ns/ontology/gist/"
GIST_HASH = "https://w3id.org/semanticarts/ns/ontology/gist#"


def _query(namespace: str, var: str) -> str:
    return (
        f"prefix gist: <{namespace}>\n"
        "prefix ex:   <https://example.org/>\n"
        f'CONSTRUCT {{ ?{var} a gist:Category . }}\n'
        f'WHERE {{ BIND(IRI("https://example.org/{var}") AS ?{var}) }}\n'
    )


def _sketch(tmp_path, names_to_namespaces) -> Graph:
    """Write the given files, run the visualiser, parse what it produced."""
    queries = tmp_path / "q"
    queries.mkdir(parents=True)
    for index, (name, namespace) in enumerate(sorted(names_to_namespaces.items())):
        (queries / name).write_text(_query(namespace, f"v{index}_IRI"), encoding="utf-8")

    out = tmp_path / "sketch.ttl"
    tarql_visualiser.visualise_folder(str(queries), str(out))
    graph = Graph()
    graph.parse(out, format="turtle")
    return graph


def _categories(graph) -> set:
    return {str(o) for _s, _p, o in graph if str(o).endswith("Category")}


# The same three files under two namings: the odd one out sorts last in one
# and first in the other. Nothing about the mappings differs.
ODD_LAST = {"a_correct.rq": GIST_SLASH, "b_correct.rq": GIST_SLASH, "z_odd.rq": GIST_HASH}
ODD_FIRST = {"a_odd.rq": GIST_HASH, "m_correct.rq": GIST_SLASH, "z_correct.rq": GIST_SLASH}


def test_the_odd_file_out_is_reported_whatever_it_is_called(tmp_path):
    """Sorting last, its `#` spelling used to be silently corrected to the
    majority's `/` -- so the one real mistake in the set disappeared."""
    assert GIST_HASH + "Category" in _categories(_sketch(tmp_path, ODD_LAST))


def test_the_correct_files_are_left_alone_whatever_it_is_called(tmp_path):
    """Sorting first, its `#` spelling used to be imposed on the two correct
    files -- so the findings that followed named the innocent ones."""
    assert GIST_SLASH + "Category" in _categories(_sketch(tmp_path, ODD_FIRST))


def test_renaming_a_file_changes_nothing(tmp_path):
    """The property the defect broke, stated directly. Both namings hold the
    same three mappings, so both must produce the same triples."""
    last = _categories(_sketch(tmp_path / "last", ODD_LAST))
    first = _categories(_sketch(tmp_path / "first", ODD_FIRST))
    assert last == first == {GIST_SLASH + "Category", GIST_HASH + "Category"}


def test_files_that_agree_produce_one_namespace(tmp_path):
    """The ordinary case, pinned so a fix that scoped too eagerly -- emitting
    a distinct scratch namespace per file, say -- would be caught here rather
    than somewhere unrelated."""
    agreed = {"a.rq": GIST_SLASH, "b.rq": GIST_SLASH, "c.rq": GIST_SLASH}
    assert _categories(_sketch(tmp_path, agreed)) == {GIST_SLASH + "Category"}


def test_a_file_declaring_no_default_prefix_does_not_inherit_one(tmp_path):
    """A block must not pick up `:` from whichever file preceded it.

    `write_turtle` synthesises a scratch default for the document when no file
    declares one. Re-declaring only each file's *own* prefixes would leave a
    file with no `:` of its own inheriting the previous file's, so the scratch
    default is re-emitted for it too.
    """
    queries = tmp_path / "q"
    queries.mkdir()
    (queries / "a_declares_default.rq").write_text(
        "prefix : <https://example.org/first/>\n"
        'CONSTRUCT { ?a_IRI a :Thing . } WHERE { BIND(IRI("https://example.org/a") AS ?a_IRI) }\n',
        encoding="utf-8",
    )
    (queries / "b_declares_none.rq").write_text(
        "prefix ex: <https://example.org/>\n"
        'CONSTRUCT { ?b_IRI a ex:Thing . } WHERE { BIND(IRI("https://example.org/b") AS ?b_IRI) }\n',
        encoding="utf-8",
    )
    out = tmp_path / "sketch.ttl"
    tarql_visualiser.visualise_folder(str(queries), str(out))
    graph = Graph()
    graph.parse(out, format="turtle")  # must not raise on an undeclared `:`

    types = {str(o) for _s, p, o in graph if str(p).endswith("type")}
    assert "https://example.org/first/Thing" in types
    assert "https://example.org/Thing" in types


def test_the_warning_no_longer_claims_a_winner(tmp_path, capsys):
    """It used to say "keeping first definition", which is no longer what
    happens and was the thing that made the behaviour sound deliberate."""
    _sketch(tmp_path, ODD_LAST)
    err = capsys.readouterr().err
    assert "keeping first definition" not in err
    assert "each file is read with its own definition" in err
    assert "gist:" in err, "the warning must still name the prefix that disagrees"
