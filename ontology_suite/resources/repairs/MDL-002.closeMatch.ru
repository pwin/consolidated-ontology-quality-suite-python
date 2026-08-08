PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
DELETE { ?focusNode owl:equivalentClass ?value . }
INSERT { ?focusNode skos:closeMatch ?value . }
WHERE { ?focusNode owl:equivalentClass ?value . }
