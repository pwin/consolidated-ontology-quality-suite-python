PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
DELETE { ?focusNode rdfs:subClassOf ?value . }
INSERT { }
WHERE { ?focusNode rdfs:subClassOf ?value . }
