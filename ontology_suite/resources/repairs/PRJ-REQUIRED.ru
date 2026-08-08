PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
INSERT { ?focusNode ?path ?taggedLabel . }
WHERE {
  FILTER(?path IN (rdfs:label, skos:prefLabel))
  BIND(STRLANG(?derivedLabel, ?defaultLanguageTag) AS ?taggedLabel)
}
