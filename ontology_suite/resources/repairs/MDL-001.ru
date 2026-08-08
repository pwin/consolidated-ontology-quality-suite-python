PREFIX owl: <http://www.w3.org/2002/07/owl#>
DELETE { ?focusNode owl:inverseOf ?value . }
INSERT { }
WHERE { ?focusNode owl:inverseOf ?value . }
