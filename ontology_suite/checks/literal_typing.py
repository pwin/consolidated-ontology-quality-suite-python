"""DAT-001, evaluated against rdflib's own parsed literal values rather
than against the stored lexical form.

``sparql/data/DAT-001.rq`` (and its ``shapes/data.ttl`` SHACL twin) test a
literal's lexical form with a regex, because that is all a portable SPARQL
engine can do: SPARQL has no "is this literal well-formed for its datatype"
operator. That formulation has two blind spots, both of which this module
covers -- it does *not* replace the portable check, it supplements it, and
the two dedup into a single finding wherever they agree
(``checks/merge.py``'s ``(check_id, focus_node, path, value)`` key).

1. **rdflib rewrites an invalid ``xsd:boolean`` before the check can see
   it.** rdflib's boolean converter is the one XSD converter that never
   raises: it warns ("Parsing weird boolean, 'yes' does not map to True or
   False") and yields ``False``. With ``rdflib.NORMALIZE_LITERALS`` on (the
   default), the literal's stored lexical form is then re-serialized *from
   that value*, so ``"yes"^^xsd:boolean`` is stored as ``'false'`` -- which
   matches the regex perfectly. The boolean branch of the regex check is,
   under rdflib, unreachable: no lexical form can survive parsing and still
   fail it. ``xsd:date``/``xsd:integer`` keep their authored lexical form
   (their converters raise, so there is no value to normalize back from),
   which is exactly why those two *are* caught by the regex and the boolean
   never was.

   The authored text is not recoverable from the parsed graph for this one
   case -- ``"yes"`` is simply gone by the time any check runs -- so the
   message says so rather than quoting the rewritten ``'false'`` as if it
   were what someone typed.

2. **A regex tests the lexical space, not the value space.** ``2020-13-45``
   and ``2021-02-30`` match ``^-?[0-9]{4}-[0-9]{2}-[0-9]{2}$`` and are
   still not dates. rdflib's own parse rejects both. Checking
   ``Literal.ill_typed`` therefore covers every XSD datatype rdflib knows
   how to parse, at full value-space precision, instead of three datatypes
   at lexical-pattern precision.

``ill_typed`` is ``True`` only when rdflib actually attempted the
conversion and it failed; it is ``None`` for a datatype rdflib has no
converter for (a custom datatype, say) and ``False`` when the literal
parsed cleanly -- so an unrecognized datatype is passed over silently
rather than guessed at.
"""
from __future__ import annotations

from rdflib import BNode, Graph, Literal
from rdflib.namespace import RDF, Namespace, XSD

SH = Namespace("http://www.w3.org/ns/shacl#")
OQ = Namespace("https://semantechs.co.uk/ontology-quality/")

CHECK_ID = "DAT-001"
SOURCE_LABEL = "literal-typing"


def _message(subject, predicate, literal: Literal) -> str:
    datatype = str(literal.datatype)
    if literal.datatype == XSD.boolean:
        # See blind spot 1 in the module docstring: quoting str(literal)
        # here would quote rdflib's rewrite, not the authored text.
        return (
            f"A literal on {subject} (via {predicate}) is not a valid "
            f"{datatype}. rdflib normalizes an invalid xsd:boolean to "
            f"'{literal}', so the lexical form as authored is no longer "
            "present in the parsed graph."
        )
    return (
        f"Literal '{literal}' on {subject} (via {predicate}) is not a valid "
        f"{datatype} -- it does not parse into that datatype's value space."
    )


def run_literal_typing_check(data_graph: Graph) -> Graph:
    """Return a SHACL results graph of ``DAT-001`` findings, one per literal
    rdflib parsed and rejected.

    Severity is hard-coded to ``sh:Violation`` to match ``DAT-001``'s
    ``default_severity`` in ``resources/registry.json`` and the literal
    ``sh:resultSeverity sh:Violation`` its two portable formulations already
    emit -- the same convention every ``.rq`` check follows.
    """
    results = Graph()
    for subject, predicate, obj in data_graph:
        if not isinstance(obj, Literal) or obj.ill_typed is not True:
            continue
        result = BNode()
        results.add((result, RDF.type, SH.ValidationResult))
        results.add((result, SH.resultSeverity, SH.Violation))
        results.add((result, SH.focusNode, subject))
        results.add((result, SH.resultPath, predicate))
        results.add((result, SH.value, obj))
        results.add((result, SH.sourceConstraintComponent, OQ[CHECK_ID]))
        results.add((result, SH.resultMessage, Literal(_message(subject, predicate, obj))))
    return results
