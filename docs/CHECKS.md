# Check catalogue

Generated from `registry.json` by `docs/generate_checks_md.py` -- 56 checks across 9 categories. Do not hand-edit; re-run the generator instead.

## Data quality (`data`)

### `DAT-001` -- Literal lexical form invalid for its datatype

- **Default severity:** Violation
- **Metric:** literal well-formedness
- **Description:** A literal is not valid for its declared datatype -- either its lexical form does not match the expected pattern (checked portably for xsd:date, xsd:integer and xsd:boolean) or it does not parse into that datatype's value space at all (checked for every XSD datatype the RDF parser validates, which also covers lexically well-formed impossibilities such as "2021-02-30"^^xsd:date).
- **Remediation:** Correct the literal so its lexical form is valid for the declared datatype, or correct the declared datatype.
- **Cucumber:** Data Quality / Typed literals are lexically valid for their datatype

### `DAT-002` -- Dangling IRI reference

- **Default severity:** Warning
- **Metric:** referential completeness of data
- **Description:** An IRI is used as the object of a triple but never appears as a subject and is never used as a class anywhere in the graph, suggesting an unresolved/dangling reference.
- **Remediation:** Add the missing description of the referenced resource, or fix the IRI if it was a typo.
- **Cucumber:** Data Quality / Every referenced resource is described somewhere in the graph

### `DAT-003` -- Duplicate literal values

- **Default severity:** Info
- **Metric:** data redundancy
- **Description:** The same subject/predicate pair has the same literal value asserted more than once.
- **Remediation:** De-duplicate the repeated literal values; if intentional (e.g. multiple language variants), differentiate with language tags instead of duplicating.
- **Cucumber:** Data Quality / Subjects do not carry duplicate literal values for the same property

### `DAT-004` -- gist:Magnitude without a unit of measure

- **Default severity:** Violation
- **Metric:** quantity completeness
- **Description:** An entity that is a SHACL instance of gist:Magnitude (the class itself or any rdfs:subClassOf* descendant) has no gist:hasUnitOfMeasure value, or has one whose target is not a gist:UnitOfMeasure. A magnitude without a unit is not a quantity: '221.78' is not a length, and the omission stays invisible until someone tries to compare or convert two of them. Pinned to gist's exact namespace (https://w3id.org/semanticarts/ns/ontology/gist/) in both the SHACL and SPARQL formulations, which is narrower than this suite's usual local-name matching of gist terms: a graph built on a pre-gist-12 vocabulary, where the property was named gist:unitOfMeasure rather than gist:hasUnitOfMeasure, is not checked rather than checked and found wanting.
- **Remediation:** Add a gist:hasUnitOfMeasure link from the magnitude to an instance typed gist:UnitOfMeasure; if the link is already present, check its target is declared a unit rather than an aspect or an untyped IRI. If the graph uses gist's pre-12 gist:unitOfMeasure spelling, widen the path in both shapes/data.ttl and sparql/data/DAT-004.rq together.
- **Cucumber:** Data Quality / Every magnitude carries a unit of measure

## Logical cogency (`logical`)

### `LOG-001` -- Class disjoint with its own ancestor

- **Default severity:** Violation
- **Metric:** logical consistency / cogency
- **Description:** A class is asserted owl:disjointWith one of its own transitive rdfs:subClassOf ancestors, which makes the class logically unsatisfiable.
- **Remediation:** Remove either the subclass axiom or the disjointness axiom; the two together are contradictory.
- **Cucumber:** Logical Cogency / No class is disjoint with one of its own superclasses

### `LOG-002` -- owl:FunctionalProperty violated by data

- **Default severity:** Violation
- **Metric:** logical consistency of data w.r.t. axioms
- **Description:** A property declared owl:FunctionalProperty has a subject with two or more distinct values, contradicting the functional-property axiom.
- **Remediation:** Either correct the data so each subject has a single value, or remove the FunctionalProperty axiom if multiple values are legitimate.
- **Cucumber:** Logical Cogency / Functional properties never have more than one distinct value per subject

### `LOG-003` -- Redundant equivalentClass + subClassOf pair

- **Default severity:** Warning
- **Metric:** axiom redundancy / hidden cycles
- **Description:** A class is declared both owl:equivalentClass and rdfs:subClassOf the same other class, which is logically redundant and may mask an unintended modelling cycle.
- **Remediation:** Keep only owl:equivalentClass (which already implies subclassing both ways) or reconsider whether subclassing was intended instead of equivalence.
- **Cucumber:** Logical Cogency / No class is redundantly both equivalent to and a subclass of the same class

### `LOG-004` -- Property has more than one inverse

- **Default severity:** Violation
- **Metric:** logical consistency of inverse axioms
- **Description:** A property is declared owl:inverseOf two distinct other properties (directly or indirectly), which is contradictory since an inverse pairing must be unique.
- **Remediation:** Remove the extra owl:inverseOf assertion so the property has at most one declared inverse.
- **Cucumber:** Logical Cogency / No property is declared inverse of two distinct other properties

### `LOG-005` -- Property declared inverse of itself

- **Default severity:** Violation
- **Metric:** logical consistency of inverse axioms
- **Description:** A property is asserted owl:inverseOf itself, a degenerate/contradictory inverse axiom.
- **Remediation:** Remove the self-referential owl:inverseOf assertion.
- **Cucumber:** Logical Cogency / No property is declared inverse of itself

### `LOG-006` -- Symmetric property has unequal domain and range

- **Default severity:** Violation
- **Metric:** logical consistency of symmetric properties
- **Description:** A property declared owl:SymmetricProperty has an rdfs:domain different from its rdfs:range, which is inconsistent for a symmetric relation.
- **Remediation:** Make the domain and range equal (e.g. both the same class, or their union), or remove the SymmetricProperty axiom if that is not intended.
- **Cucumber:** Logical Cogency / Every symmetric property has equal domain and range

### `LOG-007` -- Transitive property has unequal domain and range

- **Default severity:** Violation
- **Metric:** logical consistency of transitive properties
- **Description:** A property declared owl:TransitiveProperty has an rdfs:domain different from its rdfs:range, which is inconsistent for a transitive relation.
- **Remediation:** Make the domain and range equal (e.g. both the same class, or their union), or remove the TransitiveProperty axiom if that is not intended.
- **Cucumber:** Logical Cogency / Every transitive property has equal domain and range

## Naming style (`style`)

### `STY-001` -- Class name not UpperCamelCase

- **Default severity:** Warning
- **Metric:** naming convention consistency
- **Description:** The local name of an owl:Class does not follow UpperCamelCase (PascalCase), the de-facto convention for OWL classes.
- **Remediation:** Rename the class local name to UpperCamelCase, e.g. 'motorVehicle' -> 'MotorVehicle'.
- **Cucumber:** Naming Style / Classes are named in UpperCamelCase

### `STY-002` -- Property name not lowerCamelCase

- **Default severity:** Warning
- **Metric:** naming convention consistency
- **Description:** The local name of an owl:ObjectProperty or owl:DatatypeProperty does not follow lowerCamelCase, the de-facto convention for OWL properties.
- **Remediation:** Rename the property local name to lowerCamelCase, e.g. 'Has_Owner' -> 'hasOwner'.
- **Cucumber:** Naming Style / Properties are named in lowerCamelCase

### `STY-003` -- Label missing a language tag

- **Default severity:** Info
- **Metric:** internationalisation style
- **Description:** An rdfs:label value has no language tag, which reduces internationalisation support and can cause ambiguous display in multilingual tools.
- **Remediation:** Add a language tag to the literal, e.g. "Person"@en, or add a plain xsd:string tag deliberately if language-neutrality is intended.
- **Cucumber:** Naming Style / Labels declare an explicit language tag

### `STY-004` -- skos:prefLabel does not match local name

- **Default severity:** Warning
- **Metric:** naming convention consistency
- **Description:** A class or property's skos:prefLabel, once flattened to alphanumerics, does not match its IRI local name (also flattened), suggesting the label and IRI have drifted apart.
- **Remediation:** Align the skos:prefLabel with the term's local name, or rename the term to match its intended label.
- **Cucumber:** Naming Style / Every skos:prefLabel matches its term's local name

### `STY-005` -- Inconsistent local-name separator style

- **Default severity:** Warning
- **Metric:** naming convention consistency
- **Description:** Class and property local names across the ontology mix more than one non-alphanumeric separator style (e.g. some hyphenated, some underscored).
- **Remediation:** Pick a single separator convention (or none, for camelCase) and apply it consistently across all local names.
- **Cucumber:** Naming Style / Local names use a single, consistent separator style across the ontology

## Ontology conformance (data/sketch vs. declarations) (`conformance`)

### `CNF-001` -- Class used but not declared in the ontology

- **Default severity:** Warning
- **Metric:** ontology conformance
- **Description:** A class is used with rdf:type somewhere in a graph (a real triplified data graph, or a TARQL/oxi-gen CONSTRUCT-query sketch of one) but is never declared owl:Class/rdfs:Class in the ontology it is supposed to conform to. Shared by the 'data' pipeline stage (real data vs ontology) and the 'sketch' stage (query-shape sketch vs ontology) via ontology_suite.dataquality.data_quality.check_conformance -- the finding's 'sources' tag distinguishes which graph it came from.
- **Remediation:** Declare the class in the ontology, or fix the typo/undeclared-vocabulary use in the data or query.
- **Cucumber:** Conformance / Every class used is declared in the ontology

### `CNF-002` -- Property used but not declared in the ontology

- **Default severity:** Warning
- **Metric:** ontology conformance
- **Description:** A property is used somewhere in a graph (real data or a CONSTRUCT-query sketch) but is never declared as rdf:Property/owl:ObjectProperty/owl:DatatypeProperty/owl:AnnotationProperty in the ontology.
- **Remediation:** Declare the property in the ontology, or fix the typo/undeclared-vocabulary use.
- **Cucumber:** Conformance / Every property used is declared in the ontology

### `CNF-003` -- rdfs:domain violation

- **Default severity:** Violation
- **Metric:** ontology conformance
- **Description:** A subject uses a property whose type (nor any ancestor) matches any of the property's declared rdfs:domain classes, in a real data graph or a CONSTRUCT-query sketch.
- **Remediation:** Fix the subject's asserted type in the data/query, or add/relax the property's rdfs:domain in the ontology.
- **Cucumber:** Conformance / Every property use satisfies the property's declared rdfs:domain

### `CNF-004` -- rdfs:range violation

- **Default severity:** Violation
- **Metric:** ontology conformance
- **Description:** A property's value (literal datatype, or resource type) doesn't match any of the property's declared rdfs:range classes/datatypes, in a real data graph or a CONSTRUCT-query sketch.
- **Remediation:** Fix the value's type/datatype in the data/query, or add/relax the property's rdfs:range in the ontology.
- **Cucumber:** Conformance / Every property value satisfies the property's declared rdfs:range

### `CNF-005` -- Ontology class never populated

- **Default severity:** Info
- **Metric:** ontology conformance
- **Description:** A class the ontology declares is never used as an rdf:type in the graph under assessment. For the 'sketch' stage this commonly just means no CONSTRUCT query in the folder happens to populate that class; for a real, complete data export it may indicate a genuinely unused part of the model. Informational only.
- **Remediation:** Expected for a partial batch/sketch; investigate only if this class should always be populated by a complete export.
- **Cucumber:** Conformance / Report which declared classes, if any, a graph never populates

## Quality / documentation (`quality`)

### `QUA-001` -- Class or property missing rdfs:label

- **Default severity:** Warning
- **Metric:** documentation effectiveness
- **Description:** A class, object property or datatype property has no rdfs:label, reducing human readability of the ontology.
- **Remediation:** Add at least one rdfs:label, ideally with a language tag, e.g. rdfs:label "Person"@en.
- **Cucumber:** Documentation Quality / Every class and property has a human-readable label

### `QUA-002` -- Ontology missing versioning/title metadata

- **Default severity:** Info
- **Metric:** provenance / effectiveness metadata
- **Description:** The owl:Ontology node has no owl:versionInfo, dcterms:title or rdfs:label, harming provenance tracking and long-term effectiveness.
- **Remediation:** Add owl:versionInfo, dcterms:title/dcterms:description, and dcterms:created/modified annotations to the ontology header.
- **Cucumber:** Documentation Quality / The ontology header declares versioning and descriptive metadata

### `QUA-003` -- Deprecated term still in active use

- **Default severity:** Warning
- **Metric:** lifecycle hygiene
- **Description:** A term marked owl:deprecated true is still used as a type or predicate elsewhere in the graph.
- **Remediation:** Migrate usages to the replacement term (if any, e.g. via dcterms:isReplacedBy) and stop using the deprecated term in new data.
- **Cucumber:** Documentation Quality / Deprecated terms are not used elsewhere in the graph

### `QUA-004` -- Resource missing skos:prefLabel

- **Default severity:** Warning
- **Metric:** documentation effectiveness
- **Description:** A non-W3C-namespace resource used somewhere in the graph has no skos:prefLabel, reducing human readability and documentation quality.
- **Remediation:** Add a skos:prefLabel to the resource, ideally with a language tag.
- **Cucumber:** Documentation Quality / Every resource used in the graph has a skos:prefLabel

### `QUA-005` -- Ontology has no identifying IRI at all

- **Default severity:** Warning
- **Metric:** ontology identity
- **Description:** No resource in the graph is declared 'a owl:Ontology'. Distinct from QUA-002, which only fires once an owl:Ontology resource exists but lacks title/version/label metadata on it -- this fires when there's no such resource whatsoever.
- **Remediation:** Add '<ontologyIRI> a owl:Ontology .' identifying the ontology as a whole, distinct from any class/property IRI it defines.
- **Cucumber:** Documentation Quality / The ontology declares a resource identifying itself as an owl:Ontology

### `QUA-006` -- Ontology IRI reused as the namespace IRI for its own concepts

- **Default severity:** Warning
- **Metric:** ontology identity
- **Description:** The ontology's own identifying IRI (the subject of 'a owl:Ontology') is, once a class/property IRI's local name is stripped off, identical to that concept's namespace -- i.e. the ontology's identity and the namespace minting its terms are the same IRI. Best practice keeps these distinct (e.g. an ontology IRI with no trailing '#'/'/', and a namespace IRI that has one), so tooling and humans can tell 'the ontology as a resource' apart from 'a term it defines'.
- **Remediation:** Give the ontology its own identifying IRI distinct from the namespace IRI used to mint class/property IRIs (commonly: same base IRI, but the ontology IRI omits the trailing '#'/'/' that the namespace IRI has).
- **Cucumber:** Documentation Quality / The ontology's identifying IRI is distinct from the namespace IRI used for its own concepts

### `QUA-007` -- Ontology missing owl:versionIRI

- **Default severity:** Info
- **Metric:** ontology versioning
- **Description:** The ontology has no owl:versionIRI, making it harder for consumers/tools to pin, cache-bust, or detect exactly which version of the ontology they are using.
- **Remediation:** Add an owl:versionIRI, distinct per released version (e.g. embedding a version segment in the IRI path).
- **Cucumber:** Documentation Quality / The ontology declares an owl:versionIRI

### `QUA-008` -- Ontology or version IRI does not use the https:// scheme

- **Default severity:** Warning
- **Metric:** ontology identity
- **Description:** The ontology's identifying IRI, or its owl:versionIRI, uses a scheme other than https:// (typically plain http://). Using https guarantees the integrity/authenticity of the ontology document when it is actually dereferenced.
- **Remediation:** Mint the ontology IRI and versionIRI under an https:// base.
- **Cucumber:** Documentation Quality / The ontology's identifying and version IRIs use the https:// scheme

### `QUA-009` -- Class or property without exactly one skos:prefLabel

- **Default severity:** Warning
- **Metric:** term documentation completeness
- **Description:** A declared class or property has either no skos:prefLabel or more than one. Stricter than QUA-004, which accepts an rdfs:label instead and asks only that some label exist: this one applies to class/property declarations specifically, requires skos:prefLabel specifically, and constrains the count, because a term with several prefLabels forces any consumer rendering 'the' label of that term to choose between them arbitrarily. Anonymous class expressions (owl:Restriction and the other blank-node axiom forms) are exempt -- they are owl:Class instances that can never meaningfully carry a label.
- **Remediation:** Add a skos:prefLabel if the term has none; if it has several, keep the one canonical label and move the rest to skos:altLabel.
- **Cucumber:** Documentation Quality / Every class and property definition carries exactly one skos:prefLabel

### `QUA-010` -- Class or property without a skos:definition

- **Default severity:** Warning
- **Metric:** term documentation completeness
- **Description:** A declared class or property has no skos:definition. Distinct from STR-004, which asks whether a class is formally defined by an axiom (rdfs:subClassOf, owl:equivalentClass, a union/intersection/enumeration): that is a question about logic, this one is about prose, and a term can be fully axiomatised and still leave a reader unable to tell what it means. skos:definition specifically, not rdfs:comment -- a comment is a note to whoever maintains the ontology, a definition is the term's meaning.
- **Remediation:** Add a skos:definition stating what the term means, in prose a reader outside the authoring team can act on.
- **Cucumber:** Documentation Quality / Every class and property definition carries at least one skos:definition

## Reasoning (OWL2 profile & consistency) (`reasoning`)

### `REA-001` -- Individual in two disjoint classes (post-closure)

- **Default severity:** Violation
- **Metric:** post-closure logical consistency
- **Description:** After owlrl RDFS/OWL2-RL closure, an individual is a member of two classes declared owl:disjointWith each other. Unlike LOG-001 (which catches a class asserted disjoint with its own ancestor at the schema level), this is an instance-level contradiction that may only become visible once subclass/equivalence entailments are materialized.
- **Remediation:** Either the disjointness axiom is wrong, the individual's typing is wrong, or an upstream subclass/equivalence axiom is entailing an unintended type -- trace the closure to find which.
- **Cucumber:** Reasoning / No individual is entailed to be a member of two disjoint classes

### `REA-002` -- Asymmetric property holds in both directions

- **Default severity:** Violation
- **Metric:** post-closure logical consistency
- **Description:** A property declared owl:AsymmetricProperty is asserted (or entailed) in both directions between the same pair of individuals, which is a direct contradiction of its axiom.
- **Remediation:** Remove the incorrect direction, or reconsider whether the property should really be declared asymmetric.
- **Cucumber:** Reasoning / No asymmetric property holds in both directions between the same pair

### `REA-003` -- Irreflexive property asserted as a self-loop

- **Default severity:** Violation
- **Metric:** post-closure logical consistency
- **Description:** A property declared owl:IrreflexiveProperty is asserted (or entailed) to relate an individual to itself, which is a direct contradiction of its axiom.
- **Remediation:** Remove the self-referencing triple, or reconsider whether the property should really be declared irreflexive.
- **Cucumber:** Reasoning / No irreflexive property is asserted as a self-loop

### `REA-004` -- Individual inferred to be a member of owl:Nothing

- **Default severity:** Violation
- **Metric:** post-closure logical consistency
- **Description:** After owlrl closure, an individual is typed owl:Nothing, the empty class -- a direct sign that at least one class it is typed with is unsatisfiable given the ontology's axioms.
- **Remediation:** Pair this suite with a real OWL2 DL reasoner (see docs/REASONING.md) to identify exactly which class is unsatisfiable and why; owlrl's rule-based closure can detect that something is wrong but not always explain the minimal contradicting axiom set.
- **Cucumber:** Reasoning / No individual is inferred to be a member of owl:Nothing

### `REA-010` -- Ontology exceeds the OWL2 EL profile

- **Default severity:** Info
- **Metric:** OWL2 profile expressiveness
- **Description:** The ontology uses at least one construct not permitted in OWL2 EL (e.g. owl:unionOf, universal/cardinality restrictions beyond 0/1, or a datatype outside EL's allowed set). Informational only -- most ontologies are not intended to be EL, and this does not indicate a defect.
- **Remediation:** If EL-profile reasoning (e.g. via ELK) is a goal, see docs/REASONING.md for the specific constructs found and the axioms to revise or drop.
- **Cucumber:** Reasoning / Report which OWL2 EL-incompatible constructs, if any, the ontology uses

### `REA-011` -- Ontology exceeds the OWL2 QL profile

- **Default severity:** Info
- **Metric:** OWL2 profile expressiveness
- **Description:** The ontology uses at least one construct not permitted in OWL2 QL (e.g. existential restrictions in a superclass position, property chains, or cardinality restrictions beyond 0/1). Informational only.
- **Remediation:** If QL-profile (first-order rewritable) query answering is a goal, see docs/REASONING.md for the specific constructs found and the axioms to revise or drop.
- **Cucumber:** Reasoning / Report which OWL2 QL-incompatible constructs, if any, the ontology uses

### `REA-012` -- Ontology exceeds the OWL2 RL profile

- **Default severity:** Info
- **Metric:** OWL2 profile expressiveness
- **Description:** The ontology uses at least one construct not permitted in OWL2 RL (e.g. existentials or unions in a superclass position, or disjointness/cardinality combinations outside RL's rule-friendly forms). Informational only -- this suite's own owlrl-based reasoning backend is itself an RL-rule engine, so a non-RL ontology is exactly the case where its closure will be incomplete.
- **Remediation:** If rule-based reasoning (owlrl, or any OWL2 RL engine) needs to be complete for this ontology, see docs/REASONING.md for the specific constructs found and the axioms to revise or drop.
- **Cucumber:** Reasoning / Report which OWL2 RL-incompatible constructs, if any, the ontology uses

### `REA-020` -- Ontology found inconsistent by an external DL reasoner

- **Default severity:** Violation
- **Metric:** external DL reasoner consistency
- **Description:** A full OWL2 DL reasoner (HermiT/Pellet via owlready2, or another configured backend) reported the ontology (plus data, if included) as logically inconsistent -- there is no model that satisfies every axiom simultaneously.
- **Remediation:** Consult the reasoner's explanation (owlready2 exposes justification support) to find the minimal contradicting axiom set; owlrl's pattern-based REA-00x checks may already surface part of the same contradiction.
- **Cucumber:** Reasoning / The ontology is logically consistent according to a real OWL2 DL reasoner

### `REA-021` -- Class found unsatisfiable by an external DL reasoner

- **Default severity:** Violation
- **Metric:** external DL reasoner consistency
- **Description:** A full OWL2 DL reasoner determined that a named class can never have any instances given the ontology's axioms (it is equivalent to owl:Nothing), independent of whether any individual is actually asserted a member of it.
- **Remediation:** Review the class's superclass/disjointness/restriction axioms for a direct contradiction (e.g. disjoint with an ancestor, or a restriction requiring an unsatisfiable filler).
- **Cucumber:** Reasoning / No named class is unsatisfiable according to a real OWL2 DL reasoner

### `REA-022` -- External DL reasoner unavailable -- only owlrl-based checks ran

- **Default severity:** Info
- **Metric:** external DL reasoner availability
- **Description:** No external OWL2 DL reasoner (owlready2 + HermiT/Pellet, or a configured ELK endpoint) was available in this environment, so only the always-on owlrl RDFS/OWL2-RL closure and pattern checks (REA-001..004, LOG-001..007) ran. These are sound but not complete for full OWL2 DL -- a class can be genuinely unsatisfiable without any of them firing.
- **Remediation:** Install owlready2 plus a Java reasoner (HermiT ships with owlready2's default `sync_reasoner()`) if you need complete OWL2 DL consistency/satisfiability checking; see docs/REASONING.md.
- **Cucumber:** Reasoning / Report clearly when full DL reasoning was skipped, rather than silently only running the sound-but-incomplete checks

## Structural & runtime efficiency (`efficiency`)

### `EFF-001` -- Class hierarchy chain too deep

- **Default severity:** Warning
- **Metric:** reasoning / traversal efficiency
- **Description:** A chain of 6 or more distinct rdfs:subClassOf hops exists, which increases reasoner and query-traversal cost and often signals over-specialisation.
- **Remediation:** Flatten the hierarchy or introduce intermediate faceted classification instead of a single deep chain.
- **Cucumber:** Structural & Runtime Efficiency / Class hierarchies stay within a reasonable maximum depth

### `EFF-002` -- Excessive blank-node ratio

- **Default severity:** Warning
- **Metric:** graph identity / linkability efficiency
- **Description:** More than 20% of the nodes in the graph are blank nodes, which hinders external linkability, caching, and stable identity across loads.
- **Remediation:** Mint stable IRIs for entities that are referenced more than once or referenced externally; reserve blank nodes for genuinely anonymous, non-shared structure.
- **Cucumber:** Structural & Runtime Efficiency / Blank-node usage stays below an acceptable ratio of total nodes

### `EFF-003` -- Excessive property fan-out on a single subject

- **Default severity:** Info
- **Metric:** data-shape efficiency
- **Description:** A single subject has more than 50 values for the same non-type property, a common list/collection anti-pattern that hurts query and update efficiency.
- **Remediation:** Model the collection as RDF List/Seq/Container, split across auxiliary resources, or move high-cardinality data out of the ontology-facing graph.
- **Cucumber:** Structural & Runtime Efficiency / No subject/property pair has an excessive number of values

## Structural integrity (`structural`)

### `STR-001` -- Undefined class used as rdf:type

- **Default severity:** Violation
- **Metric:** referential integrity of class usage
- **Description:** An individual is typed with a class IRI that is never declared as owl:Class or rdfs:Class anywhere in the combined ontology+data graph.
- **Remediation:** Declare the class with 'CLASS a owl:Class', import the vocabulary that defines it, or fix the typo in the type IRI.
- **Cucumber:** Structural Integrity / Every rdf:type used on an instance resolves to a declared class

### `STR-002` -- Undefined property used

- **Default severity:** Violation
- **Metric:** referential integrity of property usage
- **Description:** A predicate is used in a triple but is never declared as rdf:Property, owl:ObjectProperty, owl:DatatypeProperty or owl:AnnotationProperty.
- **Remediation:** Declare the property's type explicitly, or import the vocabulary that defines it.
- **Cucumber:** Structural Integrity / Every predicate used resolves to a declared property

### `STR-003` -- Property missing both domain and range

- **Default severity:** Warning
- **Metric:** schema completeness
- **Description:** An owl:ObjectProperty or owl:DatatypeProperty declares neither rdfs:domain nor rdfs:range.
- **Remediation:** Add rdfs:domain and rdfs:range (or an equivalent OWL restriction) to make the property's intended usage explicit and enable stronger reasoning/validation.
- **Cucumber:** Structural Integrity / Every property declares a domain and a range

### `STR-004` -- Class has no formal definition

- **Default severity:** Warning
- **Metric:** schema completeness
- **Description:** An owl:Class has none of owl:equivalentClass, owl:intersectionOf, rdfs:subClassOf, owl:unionOf, owl:oneOf or owl:disjointWith, leaving it without any formal definition.
- **Remediation:** Give the class a formal definition, e.g. an rdfs:subClassOf axiom, an owl:equivalentClass restriction, or an explicit union/intersection/enumeration.
- **Cucumber:** Structural Integrity / Every class has at least one formal definition axiom

### `STR-005` -- Property domain value has no declared type

- **Default severity:** Violation
- **Metric:** referential integrity of schema metadata
- **Description:** An rdfs:domain value is never given an rdf:type anywhere in the graph.
- **Remediation:** Declare the domain value as a class (or fix the typo/reference), so the domain axiom actually points at a real class.
- **Cucumber:** Structural Integrity / Every rdfs:domain value resolves to a typed resource

### `STR-006` -- Untyped object of a triple

- **Default severity:** Violation
- **Metric:** referential integrity of instance usage
- **Description:** The IRI object of a triple (excluding owl:imports/owl:versionIRI/rdf:type) is never given an rdf:type anywhere in the graph.
- **Remediation:** Declare an rdf:type for the referenced resource, or fix the IRI if it was a typo.
- **Cucumber:** Structural Integrity / Every IRI object used in the graph is typed somewhere

### `STR-007` -- Predicate has no declared rdf:type

- **Default severity:** Violation
- **Metric:** referential integrity of property usage
- **Description:** A predicate used in a triple is never given any rdf:type at all (a broader check than STR-002, which only looks for the four standard property types).
- **Remediation:** Declare an rdf:type for the predicate (e.g. rdf:Property or a more specific OWL property type), or import the vocabulary that defines it.
- **Cucumber:** Structural Integrity / Every predicate used has some declared rdf:type

### `STR-008` -- Property range value has no declared type

- **Default severity:** Violation
- **Metric:** referential integrity of schema metadata
- **Description:** An owl:ObjectProperty's rdfs:range value is never given an rdf:type anywhere in the graph.
- **Remediation:** Declare the range value as a class (or fix the typo/reference), so the range axiom actually points at a real class.
- **Cucumber:** Structural Integrity / Every rdfs:range value of an object property resolves to a typed resource

### `STR-009` -- Untyped subject of a triple

- **Default severity:** Violation
- **Metric:** referential integrity of instance usage
- **Description:** The subject of a triple is never given an rdf:type anywhere in the graph.
- **Remediation:** Declare an rdf:type for the subject, or fix the IRI if it was a typo.
- **Cucumber:** Structural Integrity / Every subject used in the graph is typed somewhere

## TARQL query consistency (query source, not a graph) (`tarql`)

### `TQL-001` -- Variable bound by structurally different expressions across queries

- **Default severity:** Warning
- **Metric:** query-set consistency
- **Description:** One target variable is bound by BIND in more than one query file, using expressions that differ structurally -- compared as skeletons, with every ?var reduced to ? so that feeding the same template from a differently-named column is not reported. A structural difference means the same conceptual node is minted as two different IRIs, which does not show up as an error in either query: both are valid, both produce triples, and the two IRIs simply never join. It surfaces much later as a dangling reference or a duplicate entity, a long way from the query that caused it.
- **Remediation:** Decide which expression is correct and use it in every file, or rename the variables so the two are not mistaken for one another.
- **Cucumber:** TARQL Query Consistency / A variable bound in several queries is built the same way in each

### `TQL-002` -- Constructed-IRI variable used in CONSTRUCT but never bound

- **Default severity:** Violation
- **Metric:** query completeness
- **Description:** A variable whose name follows the constructed-IRI convention (?something_IRI) appears in a CONSTRUCT template but is never bound by a BIND nor matched in the WHERE clause. The naming convention says it is built rather than read from a CSV column, so nothing will ever bind it and every triple mentioning it is silently dropped for every input row.
- **Remediation:** Add the missing BIND, or correct the variable name if it should read a column directly.
- **Cucumber:** TARQL Query Consistency / Every constructed-IRI variable used in a CONSTRUCT template is bound in its query

### `TQL-003` -- CONSTRUCT variable not bound in the query

- **Default severity:** Info
- **Metric:** query completeness
- **Description:** A variable appears in a CONSTRUCT template but is not bound by a BIND and does not appear in the WHERE clause. This is ordinarily correct rather than a defect: TARQL binds each CSV header as a variable of the same name, so most such variables are simply columns. It is reported at Info because the only way to tell a column from a typo is to read the CSV header, which is a reviewer's judgement rather than something the query text can settle.
- **Remediation:** Check the variable against the CSV header. If there is no such column it is a typo, and the triple is silently dropped for every row.
- **Cucumber:** TARQL Query Consistency / Every unbound CONSTRUCT variable corresponds to a real CSV column
