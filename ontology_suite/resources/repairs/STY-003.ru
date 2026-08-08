PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
DELETE { ?focusNode rdfs:label ?oldLabel . }
INSERT { ?focusNode rdfs:label ?taggedLabel . }
WHERE {
  ?focusNode rdfs:label ?oldLabel .
  FILTER(LANG(?oldLabel) = "")
  BIND(STRLANG(STR(?oldLabel), ?defaultLanguageTag) AS ?taggedLabel)
}
