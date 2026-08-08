PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
INSERT { ?focusNode rdfs:label ?taggedLabel . }
WHERE {
  BIND(STRLANG(?derivedLabel, ?defaultLanguageTag) AS ?taggedLabel)
}
