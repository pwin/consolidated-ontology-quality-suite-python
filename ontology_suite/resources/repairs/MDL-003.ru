PREFIX owl: <http://www.w3.org/2002/07/owl#>
DELETE { ?focusNode a owl:Class . }
INSERT { ?focusNode a ?categoryClass . }
WHERE { ?focusNode a owl:Class . }
