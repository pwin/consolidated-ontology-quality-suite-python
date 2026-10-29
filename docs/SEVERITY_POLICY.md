# Severity policy: one ontology, several stringencies

A property with neither domain nor range is worth *knowing about* while you
are sketching a model and worth *blocking a release* over. Nothing about the
check changes between those two readings — only the stringency being applied.

`--fail-on` is the instrument for "how strict is this stage", and it is one
global threshold, so it cannot say "structural faults block, style does not".
`--severity-policy` is the instrument for "which checks does this stage care
about", and the two are meant to be used together.

```bash
ontology-quality-suite run \
  --ontology myOntology.ttl \
  --severity-policy ../policy-repo/severities.json \
  --stringency draft
```

Available on `ontology`, `checks`, `data`, `sketch` and `run`.

## The file

Stage names are yours. The suite has no opinion about them and ships no
defaults — `sketch`/`draft`/`release` below is an example, not a vocabulary.

```json
{
  "stages": {
    "sketch":  { "STR-003": "Info",    "QUA-004": "off" },
    "draft":   { "extends": "sketch",  "STR-003": "Warning" },
    "release": { "extends": "draft",   "STR-003": "Violation",
                 "STR-002": "Violation" }
  }
}
```

`extends` pulls in another stage and applies the rest on top, so three stages
that differ in two checks do not have to restate the other forty. The nearest
stage wins; a cycle is refused at load.

With no `stages` key the whole file is one unnamed stage, which is the
simplest useful policy and needs no `--stringency`:

```json
{ "STR-002": "Info" }
```

A list of single-key objects — `[{"STR-002": "Info"}]` — is accepted too,
because that is what people write by hand.

## `off` is not a severity

The suite has exactly three: `Violation`, `Warning`, `Info`. `off` is this
file's way of saying "do not ask this question at this stage", and it drops
the check's findings from the report.

It is deliberately distinct from `Info`: **`Info` means *we looked and here it
is*, `off` means *we did not ask*.** Prefer `Info` when you want the finding
visible but not blocking, and reach for `off` only when the question genuinely
does not apply yet.

## It applies to your own checks too

The policy is resolved against the registry *the run loaded*, so a
project-local check merged into `--registry` is addressable exactly as a
shipped one is. If your house rule is `HSE-001`, `{"HSE-001": "Info"}` works
with no further ceremony. See `docs/EXTENDING.md` for adding the check itself.

## What it does not do

**It does not edit any check.** The policy is applied to finished findings,
immediately before reports, counts and `--fail-on`. That placement is the
feature, not an implementation detail:

- A check written as both a SHACL shape and a portable query is **one row** by
  the time the policy sees it, so there is no way to apply a policy to half of
  it. Doing this by rewriting check files is the obvious alternative and it
  fails exactly here: rewrite `sh:resultSeverity` in the `.rq` and leave
  `sh:severity` on the shape, and **the shape wins**, in both directions,
  while `sources` still reads `shacl+sparql` so nothing reports the
  disagreement. Total findings, Violation count and exit code can all be
  identical to a correct run.
- Nothing is coupled to how the suite spells severity, and nothing freezes at
  the version it was copied from.

**It does not relabel anything quietly.** `CLAUDE.md` says: *set severity to
what is true and use `--fail-on` to decide what blocks a build; do not relabel
a finding to get a green build.* This feature does not weaken that rule, it is
what makes it enforceable at stage granularity:

- every re-mapping is **printed on the run that did it**, naming the stage and
  the file;
- `full_results.csv` gains a `declared_severity` column holding what the check
  itself said, populated only where a policy moved it — so "was this
  re-mapped?" is answerable by looking, without knowing which stage ran;
- a stage that changes nothing **warns**, because a wrong stage name is
  otherwise indistinguishable from a correct run.

So a policy can change what blocks a build, but it cannot do so invisibly.

## What it refuses

Both of these fail silently if nothing checks them, and both have been seen:

| Written | What happens |
|---|---|
| `"STR-0003": "Violation"` | refused: `STR-0003 is not a check in this registry -- did you mean STR-003?` |
| `"STR-002": "Not a worry"` | refused: `'Not a worry' is not a severity. Use one of Violation, Warning, Info, or 'off'` |
| `--stringency` with no `--severity-policy` | refused: there is no default policy to take a stage from |
| a stage name that does not exist | refused, listing the stages that do |
| several stages and no `--stringency` | refused rather than picking one |

A bad policy exits **2**, not 1, so CI can tell "the policy is wrong" from
"findings reached `--fail-on`".

## Reading the output

```
Severity policy 'draft' from severities.json: 3 check(s) re-mapped
  QUA-004 Warning -> off (1 finding(s) dropped)
  STR-002 Violation -> Warning (1 finding(s))
  STR-003 Warning -> Info (1 finding(s))
Findings: 12 total (1 Violation, 5 Warning, 6 Info)
```

The counts, the written reports and the exit code all describe the same rows,
because the policy is applied once, before any of them.
