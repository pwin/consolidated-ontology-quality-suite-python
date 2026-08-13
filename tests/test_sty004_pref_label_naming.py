"""Covers STY-004 (ontology_suite/resources/sparql/style/STY-004.rq):
"skos:prefLabel does not match local name, once both are flattened to
alphanumerics" -- per registry.json's own description.

Found by reviewing the query directly: the local-name side only stripped
underscores (`REPLACE(..., "_", "")`) before lowercasing, while the label
side stripped every non-alphanumeric character
(`REPLACE(..., "[^A-Za-z0-9]", "")`). Any local name using a separator
other than "_" (a hyphen being the obvious one -- valid and common in IRI
local names) survived flattening with that character still in it, while
the same word-separator in the label was already stripped -- so
`ex:record-type` labelled "Record Type" flattened to "record-type" vs.
"recordtype" and false-positived as "drifted", even though the two
obviously denote the same name. Fixed by flattening the local name with
the same `[^A-Za-z0-9]` pattern as the label, matching the registry's own
"flattened to alphanumerics" wording for both sides.

Verified this doesn't change the real gist-vehicle regression fixture's
finding count (examples/vehicle/, see test_vehicle_gist_checks.py) --
none of gist's 9 real STY-004 findings involve a local name with a
non-underscore separator, so they're unaffected by this fix.
"""
import rdflib

from ontology_suite import config

STY_004_QUERY = (config.PACKAGE_RESOURCES / "sparql" / "style" / "STY-004.rq").read_text(encoding="utf-8")
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
EX = "https://example.org/demo/"


def _findings(turtle: str):
    g = rdflib.Graph()
    g.parse(
        data=(
            f"@prefix ex: <{EX}> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
        )
        + turtle,
        format="turtle",
    )
    result = g.query(STY_004_QUERY)
    assert result.graph is not None
    return {
        str(result.graph.value(s, SH.focusNode))
        for s in result.graph.subjects(rdflib.RDF.type, SH.ValidationResult)
    }


def test_hyphenated_local_name_matching_a_space_separated_label_is_not_flagged():
    """The false positive this bug caused: a hyphen in the local name is
    exactly the kind of separator a prefLabel wouldn't carry, and the two
    are the same name once both are flattened to alphanumerics."""
    findings = _findings('ex:record-type a owl:DatatypeProperty ; skos:prefLabel "Record Type"@en .\n')
    assert findings == set()


def test_underscore_local_name_matching_a_space_separated_label_is_not_flagged():
    findings = _findings('ex:has_skill a owl:DatatypeProperty ; skos:prefLabel "Has Skill"@en .\n')
    assert findings == set()


def test_camel_case_local_name_matching_a_title_case_label_is_not_flagged():
    findings = _findings('ex:hasOwner a owl:ObjectProperty ; skos:prefLabel "Has Owner"@en .\n')
    assert findings == set()


def test_genuinely_drifted_label_is_still_flagged():
    findings = _findings('ex:driftedName a owl:DatatypeProperty ; skos:prefLabel "Totally Different"@en .\n')
    assert findings == {EX + "driftedName"}
