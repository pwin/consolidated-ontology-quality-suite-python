PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
DELETE { ?focusNode owl:equivalentClass ?value . }
INSERT { ?focusNode rdfs:subClassOf ?value . }
WHERE { ?focusNode owl:equivalentClass ?value . }
