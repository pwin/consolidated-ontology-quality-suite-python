PREFIX owl: <http://www.w3.org/2002/07/owl#>
INSERT { ?focusNode owl:versionIRI ?versionIri . }
WHERE {
  BIND(IRI(CONCAT(STR(?focusNode), "/", ?defaultVersionInfo)) AS ?versionIri)
}
