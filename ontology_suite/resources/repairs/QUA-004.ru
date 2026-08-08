PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
INSERT { ?focusNode skos:prefLabel ?taggedLabel . }
WHERE {
  BIND(STRLANG(?derivedLabel, ?defaultLanguageTag) AS ?taggedLabel)
}
