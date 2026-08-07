PREFIX owl: <http://www.w3.org/2002/07/owl#>
DELETE { ?focusNode owl:equivalentClass ?value . }
INSERT { }
WHERE { ?focusNode owl:equivalentClass ?value . }
