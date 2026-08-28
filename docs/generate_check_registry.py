"""Regenerates docs/check-registry.html from ontology_suite/resources/.

Run after adding, removing or editing any check:

    python docs/generate_check_registry.py

The companion to `generate_checks_md.py`: that one produces the terse
per-check Markdown reference for people already inside the repo, this one
produces the standalone explanatory page -- what the panel is, how the two
engine formulations relate, the full catalogue, and how to add checks of
your own. Both read the same `registry.json`, so neither can drift from the
suite; the counts, severities and prose on the page are read from the
resource tree rather than restated.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from ontology_suite import config  # noqa: E402

RES = config.PACKAGE_RESOURCES
OUT_PATH = REPO_ROOT / "docs" / "check-registry.html"

registry = json.loads(config.DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
checks = registry["checks"]

rq_paths = {p.stem: p.relative_to(RES).as_posix() for p in config.DEFAULT_SPARQL_DIR.rglob("*.rq")}
shapes_text = {p.stem: p.read_text(encoding="utf-8") for p in config.DEFAULT_SHAPES_DIR.glob("*.ttl")}


def shape_file(check_id):
    for stem, text in shapes_text.items():
        if check_id in text:
            return f"shapes/{stem}.ttl"
    return None


CATEGORIES = [
    ("structural", "Structural integrity",
     "Does every term the graph uses actually resolve to something declared? These are the "
     "referential-integrity checks -- the cheapest to run and the ones that catch typos, stale "
     "namespaces and half-finished refactors before anything subtler is worth looking at."),
    ("logical", "Logical cogency",
     "Do the axioms contradict each other, or does the data contradict the axioms? These read the "
     "ontology as a set of claims and look for pairs that cannot both hold."),
    ("quality", "Documentation and metadata quality",
     "Is the ontology usable by someone who did not write it? Labels, definitions, versioning and "
     "a coherent identity for the ontology as a published resource."),
    ("efficiency", "Structural and runtime efficiency",
     "Shapes that are legal but expensive -- for reasoners, for query planners, and for anyone "
     "trying to link to the graph from outside."),
    ("style", "Naming and style",
     "Conventions rather than correctness. Every finding here is defensible to ignore; what they "
     "buy is a codebase where the exceptions are visible."),
    ("data", "Data quality",
     "Checks that examine literal values and references in a populated graph, rather than the "
     "schema that governs them."),
    ("reasoning", "Reasoning and profiles",
     "Findings that only exist after inference, plus OWL2 profile membership and whatever a real "
     "DL reasoner adds beyond the always-on rule-based closure."),
    ("conformance", "Conformance against a separate ontology",
     "The only group that compares two things rather than examining one. A data graph, or a "
     "query-shape sketch of one, is diffed against the declarations of the ontology it is "
     "supposed to conform to."),
    ("tarql", "TARQL query consistency",
     "The only group that reads query <em>source</em> rather than a graph. A folder of TARQL "
     "queries is a program, and like any program it drifts: the same conceptual IRI ends up minted "
     "two ways in two files, and nothing about either query is invalid. These findings come from "
     "the query text, so no SPARQL or SHACL formulation of them is possible."),
]

CLOSURE_SAFE = {"LOG-001", "LOG-002", "LOG-004", "LOG-005"}
SEV_CLASS = {"Violation": "violation", "Warning": "warning", "Info": "info"}


def esc(s):
    return html.escape(s, quote=False)


def render_check(c):
    cid = c["id"]
    sev = c["default_severity"]
    rq = rq_paths.get(cid)
    shp = shape_file(cid)

    badges = []
    if rq:
        badges.append('<span class="badge badge-sparql">SPARQL</span>')
    if shp:
        badges.append('<span class="badge badge-shacl">SHACL</span>')
    if not rq and not shp:
        badges.append('<span class="badge badge-native">Python</span>')
    if cid in CLOSURE_SAFE:
        badges.append('<span class="badge badge-closure">re-run post-closure</span>')

    sources = []
    if rq:
        sources.append(esc(rq))
    if shp:
        sources.append(esc(shp))
    src_line = (
        f'<p class="src">{" &middot; ".join(sources)}</p>' if sources
        else '<p class="src">no query file &mdash; emitted directly as a ResultRow</p>'
    )

    return f"""<article class="check sev-{SEV_CLASS[sev]}" id="{cid}">
<header class="check-head">
<span class="cid">{cid}</span>
<h4>{esc(c["title"])}</h4>
<span class="pill pill-{SEV_CLASS[sev]}">{sev}</span>
</header>
<p class="metric">{esc(c["metric"])}</p>
<p class="desc">{esc(c["description"])}</p>
<p class="fix"><span class="fix-label">Fix</span>{esc(c["remediation"])}</p>
<div class="check-foot">{"".join(badges)}</div>
{src_line}
</article>"""


sections = []
for slug, heading, blurb in CATEGORIES:
    items = [c for c in checks if c["category"] == slug]
    counts = {}
    for c in items:
        counts[c["default_severity"]] = counts.get(c["default_severity"], 0) + 1
    tally = " &middot; ".join(
        f"{n} {s.lower()}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    body = "\n".join(render_check(c) for c in items)
    sections.append(f"""<section class="cat" id="cat-{slug}">
<div class="cat-head">
<h3>{heading}</h3>
<p class="cat-meta"><span class="mono">{slug}</span> &middot; {len(items)} checks &middot; {tally}</p>
<p class="cat-blurb">{blurb}</p>
</div>
{body}
</section>""")

catalogue = "\n".join(sections)

toc_items = "\n".join(
    f'<li><a href="#cat-{slug}">{heading}</a> <span class="toc-n">{sum(1 for c in checks if c["category"] == slug)}</span></li>'
    for slug, heading, _ in CATEGORIES
)

n_total = len(checks)
n_sparql = len(rq_paths)
n_shacl = sum(1 for c in checks if shape_file(c["id"]))
n_native = n_total - n_sparql

SHACL_EXAMPLE = 'oq:STR-003\n  a sh:NodeShape ;\n  rdfs:label "STR-003: property missing both domain and range" ;\n  sh:severity sh:Warning ;                 # on the shape, never on the constraint\n  sh:target [\n    a sh:SPARQLTarget ;\n    sh:select """SELECT ?this WHERE { ?this a owl:ObjectProperty }""" ;\n  ] ;\n  sh:sparql [\n    a sh:SPARQLConstraint ;\n    sh:message "{$this} declares neither domain nor range." ;\n    sh:select """SELECT $this WHERE { ... }""" ;\n  ] .'

DOC = f"""<title>The Check Registry</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Literata:opsz,wght@7..72,400;7..72,600;7..72,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --ground:      #f6f7f9;
  --surface:     #ffffff;
  --surface-alt: #eef0f5;
  --ink:         #1a1d26;
  --ink-mid:     #454b5c;
  --ink-mute:    #6b7285;
  --rule:        #dde1e9;
  --rule-soft:   #e8ebf1;
  --accent:      #34478f;
  --accent-soft: #e7eaf6;
  --violation:   #a32e2e;
  --violation-bg:#f7e9e8;
  --warning:     #8a5e14;
  --warning-bg:  #f8efdd;
  --info:        #3e6b87;
  --info-bg:     #e6eff4;
  --shadow:      0 1px 2px rgba(26, 29, 38, .05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:      #101219;
    --surface:     #171a22;
    --surface-alt: #1e222c;
    --ink:         #e7e9f0;
    --ink-mid:     #b6bccb;
    --ink-mute:    #8d95a8;
    --rule:        #2b303c;
    --rule-soft:   #232833;
    --accent:      #93a6ea;
    --accent-soft: #1e2440;
    --violation:   #e39089;
    --violation-bg:#33211f;
    --warning:     #d9ad61;
    --warning-bg:  #2e2617;
    --info:        #85b3ce;
    --info-bg:     #1a2730;
    --shadow:      0 1px 2px rgba(0, 0, 0, .3);
  }}
}}
:root[data-theme="dark"] {{
  --ground:      #101219;
  --surface:     #171a22;
  --surface-alt: #1e222c;
  --ink:         #e7e9f0;
  --ink-mid:     #b6bccb;
  --ink-mute:    #8d95a8;
  --rule:        #2b303c;
  --rule-soft:   #232833;
  --accent:      #93a6ea;
  --accent-soft: #1e2440;
  --violation:   #e39089;
  --violation-bg:#33211f;
  --warning:     #d9ad61;
  --warning-bg:  #2e2617;
  --info:        #85b3ce;
  --info-bg:     #1a2730;
  --shadow:      0 1px 2px rgba(0, 0, 0, .3);
}}

* {{ box-sizing: border-box; }}

body {{
  background: var(--ground);
  color: var(--ink);
  font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.62;
  margin: 0;
  -webkit-font-smoothing: antialiased;
}}

.wrap {{
  max-width: 1240px;
  margin: 0 auto;
  padding: 0 clamp(20px, 4vw, 56px) 96px;
}}

/* ---------- masthead ---------- */
.masthead {{
  padding: clamp(48px, 8vw, 88px) 0 40px;
  border-bottom: 2px solid var(--ink);
  display: flex;
  flex-direction: column;
  gap: 22px;
}}
.eyebrow {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11.5px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0;
}}
h1 {{
  font-family: Literata, Georgia, serif;
  font-weight: 700;
  font-size: clamp(2.1rem, 5.4vw, 3.4rem);
  line-height: 1.08;
  letter-spacing: -.018em;
  text-wrap: balance;
  margin: 0;
}}
.standfirst {{
  font-size: clamp(1.02rem, 1.9vw, 1.16rem);
  color: var(--ink-mid);
  max-width: 66ch;
  margin: 0;
}}
.stats {{
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin-top: 8px;
  border-top: 1px solid var(--rule);
}}
.stat {{
  padding: 16px 30px 14px 0;
  margin-right: 30px;
  border-right: 1px solid var(--rule);
}}
.stat:last-child {{ border-right: 0; margin-right: 0; }}
.stat b {{
  display: block;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.55rem;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
  color: var(--ink);
}}
.stat span {{
  font-size: 12px;
  color: var(--ink-mute);
  letter-spacing: .02em;
}}

/* ---------- layout ---------- */
.cols {{ display: block; }}
@media (min-width: 1080px) {{
  .cols {{
    display: grid;
    grid-template-columns: 216px minmax(0, 1fr);
    gap: 64px;
    align-items: start;
  }}
  .rail {{ position: sticky; top: 28px; }}
}}

.rail {{
  padding-top: 44px;
  font-size: 13.5px;
}}
.rail h2 {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--ink-mute);
  font-weight: 500;
  margin: 0 0 12px;
}}
.rail ol, .rail ul {{ list-style: none; margin: 0 0 26px; padding: 0; }}
.rail li {{
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 0;
  border-bottom: 1px solid var(--rule-soft);
}}
.rail a {{ color: var(--ink-mid); text-decoration: none; }}
.rail a:hover, .rail a:focus-visible {{ color: var(--accent); }}
.toc-n {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11.5px;
  color: var(--ink-mute);
  font-variant-numeric: tabular-nums;
}}

main {{ padding-top: 44px; min-width: 0; }}

/* ---------- prose ---------- */
h2 {{
  font-family: Literata, Georgia, serif;
  font-size: clamp(1.5rem, 3vw, 1.92rem);
  font-weight: 600;
  letter-spacing: -.012em;
  line-height: 1.2;
  text-wrap: balance;
  margin: 0 0 18px;
  padding-top: 8px;
}}
section.band {{ padding: 46px 0; border-top: 1px solid var(--rule); }}
section.band:first-of-type {{ border-top: 0; padding-top: 8px; }}
h3 {{
  font-family: Literata, Georgia, serif;
  font-size: 1.22rem;
  font-weight: 600;
  line-height: 1.25;
  text-wrap: balance;
  margin: 32px 0 12px;
}}
h4 {{ font-size: 1rem; font-weight: 600; margin: 0; line-height: 1.35; }}
p {{ margin: 0 0 16px; max-width: 68ch; }}
main ul, main ol {{ max-width: 68ch; padding-left: 22px; margin: 0 0 18px; }}
main li {{ margin-bottom: 9px; }}
a {{ color: var(--accent); text-underline-offset: 2px; }}
strong {{ font-weight: 600; }}
.mono, code {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .875em;
}}
code {{
  background: var(--surface-alt);
  padding: .1em .34em;
  border-radius: 3px;
}}
pre {{
  background: var(--surface);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  border-radius: 0 4px 4px 0;
  padding: 16px 18px;
  overflow-x: auto;
  margin: 0 0 20px;
  box-shadow: var(--shadow);
}}
pre code {{ background: none; padding: 0; font-size: 12.8px; line-height: 1.62; }}
.lede {{
  font-size: 1.06rem;
  color: var(--ink-mid);
  border-left: 3px solid var(--accent);
  padding-left: 18px;
  margin-bottom: 26px;
}}
.note {{
  background: var(--surface-alt);
  border-radius: 4px;
  padding: 16px 18px;
  margin: 0 0 22px;
  font-size: .95rem;
  max-width: 68ch;
}}
.note p:last-child {{ margin-bottom: 0; }}
.note-label {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10.5px;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
  display: block;
  margin-bottom: 6px;
}}

/* ---------- tables ---------- */
.tbl-scroll {{ overflow-x: auto; margin: 0 0 24px; }}
table {{ border-collapse: collapse; width: 100%; font-size: .92rem; min-width: 460px; }}
th, td {{
  text-align: left;
  padding: 10px 16px 10px 0;
  border-bottom: 1px solid var(--rule-soft);
  vertical-align: top;
}}
th {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10.5px;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--ink-mute);
  font-weight: 500;
  border-bottom: 1px solid var(--rule);
  white-space: nowrap;
}}

/* ---------- catalogue ---------- */
.cat {{ margin-bottom: 8px; }}
.cat-head {{ padding: 34px 0 18px; }}
.cat-head h3 {{ margin: 0 0 6px; font-size: 1.34rem; }}
.cat-meta {{
  font-size: 12px;
  color: var(--ink-mute);
  margin: 0 0 12px;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  letter-spacing: .02em;
}}
.cat-blurb {{ color: var(--ink-mid); margin: 0; font-size: .96rem; }}

.check {{
  background: var(--surface);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--ink-mute);
  border-radius: 0 4px 4px 0;
  padding: 18px 20px 16px;
  margin-bottom: 12px;
  box-shadow: var(--shadow);
}}
.check.sev-violation {{ border-left-color: var(--violation); }}
.check.sev-warning   {{ border-left-color: var(--warning); }}
.check.sev-info      {{ border-left-color: var(--info); }}
.check-head {{
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-bottom: 6px;
}}
.cid {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .82rem;
  font-weight: 500;
  color: var(--accent);
  letter-spacing: .01em;
}}
.check-head h4 {{ flex: 1 1 auto; min-width: 190px; }}
.pill {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .09em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 3px;
  white-space: nowrap;
}}
.pill-violation {{ background: var(--violation-bg); color: var(--violation); }}
.pill-warning   {{ background: var(--warning-bg);   color: var(--warning); }}
.pill-info      {{ background: var(--info-bg);      color: var(--info); }}
.metric {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: var(--ink-mute);
  margin: 0 0 10px;
  max-width: none;
}}
.desc {{ margin: 0 0 10px; font-size: .95rem; }}
.fix {{ margin: 0 0 12px; font-size: .92rem; color: var(--ink-mid); }}
.fix-label {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .11em;
  text-transform: uppercase;
  color: var(--ink-mute);
  margin-right: 9px;
}}
.check-foot {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
.badge {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: .07em;
  padding: 2.5px 7px;
  border-radius: 3px;
  border: 1px solid var(--rule);
  color: var(--ink-mute);
}}
.badge-sparql  {{ border-color: var(--accent); color: var(--accent); }}
.badge-shacl   {{ background: var(--accent-soft); border-color: transparent; color: var(--accent); }}
.badge-native  {{ border-style: dashed; }}
.badge-closure {{ border-style: dotted; }}
.src {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--ink-mute);
  margin: 0;
  max-width: none;
  overflow-wrap: anywhere;
}}

/* ---------- steps ---------- */
.steps {{ counter-reset: step; list-style: none; padding: 0; margin: 0 0 24px; max-width: 68ch; }}
.steps > li {{
  counter-increment: step;
  position: relative;
  padding-left: 44px;
  margin-bottom: 26px;
}}
.steps > li::before {{
  content: counter(step);
  position: absolute;
  left: 0;
  top: 1px;
  width: 27px;
  height: 27px;
  border: 1px solid var(--accent);
  border-radius: 50%;
  color: var(--accent);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}}
.steps h4 {{ margin-bottom: 7px; }}
.steps p, .steps ul {{ margin-bottom: 12px; }}

footer {{
  border-top: 1px solid var(--rule);
  margin-top: 56px;
  padding-top: 22px;
  font-size: 12.5px;
  color: var(--ink-mute);
}}
footer p {{ max-width: 68ch; }}

a:focus-visible, summary:focus-visible {{
  outline: 2px solid var(--accent);
  outline-offset: 3px;
  border-radius: 2px;
}}
@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; transition: none !important; }}
}}
</style>

<div class="wrap">

<header class="masthead">
  <p class="eyebrow">Ontology Quality Suite &middot; check reference</p>
  <h1>The Check Registry</h1>
  <p class="standfirst">Every quality and conformance check the suite runs, what each one actually
  asserts, which of them are written in SPARQL and which in SHACL &mdash; and how to add your own
  to the panel without forking anything.</p>
  <div class="stats">
    <div class="stat"><b>{n_total}</b><span>checks in the registry</span></div>
    <div class="stat"><b>{n_sparql}</b><span>SPARQL formulations</span></div>
    <div class="stat"><b>{n_shacl}</b><span>with a SHACL twin</span></div>
    <div class="stat"><b>{n_native}</b><span>native Python checks</span></div>
  </div>
</header>

<div class="cols">
<nav class="rail" aria-label="Contents">
  <h2>Contents</h2>
  <ul>
    <li><a href="#how">How the panel runs</a></li>
    <li><a href="#contract">The result contract</a></li>
    <li><a href="#engines">Two formulations</a></li>
  </ul>
  <h2>Catalogue</h2>
  <ul>
{toc_items}
  </ul>
  <h2>Extending</h2>
  <ul>
    <li><a href="#add-sparql">Add a SPARQL check</a></li>
    <li><a href="#add-shacl">Add a SHACL twin</a></li>
    <li><a href="#add-native">Add a native check</a></li>
    <li><a href="#own-panel">Run your own panel</a></li>
    <li><a href="#pitfalls">Pitfalls</a></li>
  </ul>
</nav>

<main>

<section class="band" id="how">
<h2>How the panel runs</h2>
<p class="lede">One registry, three ways of producing a finding. Everything downstream &mdash; the
HTML report, the CSV, the Cucumber features, the severity gate that sets the exit code &mdash;
consumes a single row type, so where a finding came from never leaks into how it is reported.</p>

<p><code>registry.json</code> is the single source of truth. It holds no logic: each entry is an id,
a category, a severity, the prose shown to whoever reads the finding, and the Cucumber
feature/scenario names. A check's <em>implementation</em> lives elsewhere, and there are three
kinds:</p>

<div class="tbl-scroll">
<table>
<thead><tr><th>Kind</th><th>Lives in</th><th>Count</th><th>Use when</th></tr></thead>
<tbody>
<tr><td><strong>Portable SPARQL</strong></td><td><code>sparql/&lt;category&gt;/&lt;id&gt;.rq</code></td><td>{n_sparql}</td>
<td>The condition is a graph pattern over one merged graph. This is the default and covers most checks.</td></tr>
<tr><td><strong>SHACL shape</strong></td><td><code>shapes/&lt;category&gt;.ttl</code></td><td>{n_shacl}</td>
<td>Always paired with a SPARQL twin, never alone &mdash; the pair exists to cross-validate.</td></tr>
<tr><td><strong>Native Python</strong></td><td><code>reasoning/</code>, <code>dataquality/</code></td><td>{n_native}</td>
<td>There is no single graph to pattern-match: profile membership, an external reasoner's verdict,
or a comparison against a <em>separate</em> ontology.</td></tr>
</tbody>
</table>
</div>

<p>Discovery is by directory walk, not by manifest. <code>sparql_runner</code> globs
<code>sparql/**/*.rq</code> and <code>shacl_runner</code> loads every <code>shapes/*.ttl</code>, so
adding a file is enough to add a check and deleting one is enough to remove it. The registry entry
supplies the prose; a query with no matching entry, or an entry with no implementation, is the kind
of drift the coverage tests exist to catch.</p>
</section>

<section class="band" id="contract">
<h2>The result contract</h2>
<p>Both engines are made to speak SHACL's own vocabulary, which is what lets their output merge.
A portable SPARQL check is a <code>CONSTRUCT</code> that builds a
<code>sh:ValidationResult</code> by hand:</p>

<pre><code>CONSTRUCT {{
  _:r a sh:ValidationResult ;
    sh:resultSeverity sh:Warning ;
    sh:focusNode ?p ;
    sh:sourceConstraintComponent oq:STR-003 ;
    sh:resultMessage ?msg .
}}
WHERE {{
  {{ ?p a owl:ObjectProperty }} UNION {{ ?p a owl:DatatypeProperty }}
  FILTER NOT EXISTS {{ ?p rdfs:domain ?d }}
  FILTER NOT EXISTS {{ ?p rdfs:range ?r }}
  BIND(CONCAT("Property ", STR(?p), " declares neither domain nor range.") AS ?msg)
}}</code></pre>

<p>Four properties are mandatory: <code>sh:resultSeverity</code>, <code>sh:focusNode</code>,
<code>sh:resultMessage</code>, and <code>sh:sourceConstraintComponent oq:&lt;id&gt;</code> &mdash;
that last one is how a bare CONSTRUCT result gets tied back to its registry entry. Add
<code>sh:resultPath</code> and <code>sh:value</code> when there is a natural predicate or offending
value. Binding several of either on one result is legitimate and sometimes the honest thing to do:
<code>LOG-004</code> names both inverses it is complaining about. They are sorted and joined on the
way in, so a finding renders identically however the engine happened to order them.</p>

<p>Everything then normalises to one <code>ResultRow</code>:</p>

<pre><code>ResultRow(check_id, category, title, severity,
          focus_node, path, value,
          message, remediation, sources)</code></pre>

<p>Rows are deduplicated on <code>(check_id, focus_node, path, value)</code>. When both engines
find the same thing, one row survives carrying <code>sources: ["shacl", "sparql"]</code> &mdash;
which is precisely the signal described next.</p>
</section>

<section class="band" id="engines">
<h2>Why two formulations of the same check</h2>
<p>Eighteen checks are written twice: once as a SPARQL <code>CONSTRUCT</code>, once as a SHACL
shape. The duplication is deliberate. Running both and merging on that dedup key means a check that
fires from one engine but not the other shows up as a row with only one entry in
<code>sources</code> &mdash; a visible sign the two formulations have drifted apart, rather than a
silent disagreement about what the check means.</p>

<p><code>--engine</code> selects what actually runs:</p>

<div class="tbl-scroll">
<table>
<thead><tr><th>Value</th><th>Runs</th><th>Notes</th></tr></thead>
<tbody>
<tr><td><code>both</code></td><td>pyshacl + portable SPARQL</td><td>Full cross-validation. The default when the native engine is absent.</td></tr>
<tr><td><code>sparql</code></td><td>portable SPARQL only</td><td>Same real findings, no drift signal, roughly 8&times; faster.</td></tr>
<tr><td><code>shacl</code></td><td>pyshacl only</td><td>For symmetry; strictly slower with no broader coverage.</td></tr>
<tr><td><code>native</code></td><td>native Rust SHACL engine</td><td>Optional package. Verified to find exactly what pyshacl finds.</td></tr>
<tr><td><code>native+sparql</code></td><td>native + portable SPARQL</td><td>The fast analogue of <code>both</code>. Default when installed.</td></tr>
</tbody>
</table>
</div>

<div class="note">
<span class="note-label">Why sparql-only is safe</span>
<p>Every check in the registry-driven suite has a SPARQL formulation; only a subset also has a SHACL
one, and there is no check implemented in SHACL alone. So <code>--engine sparql</code> finds the same
set of real findings &mdash; it forfeits the drift signal, not coverage. A test asserts the
SHACL-only set stays empty, because a future SHACL-only check would quietly break that guarantee.</p>
</div>

<p>The speed gap is not subtle: measured over a real 3,300-triple ontology, the same ~50-check pass
took about 193 seconds under pyshacl against about 27 seconds for the portable layer, because
pyshacl spends most of its time on Python-level shape traversal layered on top of the same SPARQL
execution. Cross-validate in CI; use <code>sparql</code> or <code>native+sparql</code> while
iterating.</p>
</section>

<section class="band">
<h2>The catalogue</h2>
<p class="lede">All {n_total} checks, grouped by category. Severity is the registry default and can be
overridden per project; the badges say which formulations exist for each check and where to find
them.</p>
{catalogue}
</section>

<section class="band">
<h3>A note on the closure-safe split</h3>
<p>Four logical checks live in <code>sparql/logical/closure-safe/</code> rather than beside their
siblings, because the reasoning layer re-runs only that subdirectory (plus
<code>sparql/reasoning/</code>) against the owlrl deductive closure. Both directories are discovered
normally by the ordinary checks stage; the split changes only what gets re-run after inference.</p>
<p>The distinction is real, not organisational. <code>LOG-001</code>, <code>LOG-002</code>,
<code>LOG-004</code> and <code>LOG-005</code> describe genuine contradictions that stay
contradictions whether the triples producing them were asserted or entailed &mdash; catching those
post-closure is the whole point of the reasoning pass. The others describe how the ontology was
<em>authored</em>, and become meaningless once inference has run: <code>LOG-003</code> flags a
redundantly authored <code>equivalentClass</code>/<code>subClassOf</code> pair, but OWL2 RL entails
that reciprocal <code>subClassOf</code> from every <code>equivalentClass</code> unconditionally. Run
post-closure against a real gist-importing ontology, it fired on 59 of 59 axioms, none of them
authored redundantly. <code>LOG-006</code> and <code>LOG-007</code> fail the same way through
<code>rdfs:subPropertyOf</code> inheriting a superproperty's domain and range.</p>
</section>

<section class="band" id="add-sparql">
<h2>Adding a check</h2>
<p class="lede">Pick the lightest of the three kinds that fits. Most checks are a graph pattern, and
a graph pattern is a SPARQL check.</p>

<h3>A portable SPARQL check</h3>
<ol class="steps">
<li>
<h4>Pick an id and category</h4>
<p>Ids follow <code>&lt;PREFIX&gt;-&lt;NNN&gt;</code>. In use today: <code>STR</code> structural,
<code>LOG</code> logical, <code>QUA</code> quality, <code>EFF</code> efficiency, <code>STY</code>
style, <code>DAT</code> data, <code>REA</code> reasoning, <code>CNF</code> conformance. A new
category is fine &mdash; add it to the registry and to <code>CATEGORY_TITLES</code> in
<code>docs/generate_checks_md.py</code>.</p>
</li>
<li>
<h4>Add the registry entry</h4>
<pre><code>{{
  "id": "STY-006",
  "category": "style",
  "metric": "short description of what is measured",
  "default_severity": "Warning",
  "title": "One-line summary",
  "description": "What condition is being flagged, in prose.",
  "remediation": "What the reader should do about it.",
  "cucumber_feature": "Naming Style",
  "cucumber_scenario": "One sentence, phrased as an expectation that should hold"
}}</code></pre>
<p>The scenario line is phrased as the expectation, not the failure &mdash; it is rendered as a
Gherkin <code>Then</code>, so it reads as something that should be true.</p>
</li>
<li>
<h4>Write <code>sparql/&lt;category&gt;/&lt;id&gt;.rq</code></h4>
<p>Follow the result contract above. Keep it self-contained: its own <code>PREFIX</code>
declarations, no reliance on external state. Copy a neighbour in the same category as a starting
point.</p>
</li>
<li>
<h4>Run it against a graph that should fire, and one that should not</h4>
<pre><code>from rdflib import Graph
g = Graph(); g.parse("fixture.ttl", format="turtle")
q = open("sparql/style/STY-006.rq", encoding="utf-8").read()
print(len(list(g.query(q).graph)))</code></pre>
<p>A query that looks right but matches nothing against a fixture that should trigger it is a bug,
not a pass. This suite's own <code>REA-001</code> shipped with exactly that: a
<code>FILTER(STR(?c1) &lt; STR(?c2))</code> assuming <code>owl:disjointWith</code> gets symmetrised
by reasoning, which it does not.</p>
</li>
<li>
<h4>Regenerate the docs</h4>
<pre><code>python docs/generate_checks_md.py</code></pre>
<p>Nothing else to wire. The runners discover the file, the registry resolves the id, and the check
appears in every table, plot and Cucumber feature automatically.</p>
</li>
</ol>
</section>

<section class="band" id="add-shacl">
<h3>Adding a SHACL twin</h3>
<p>Optional but recommended, since it is what earns the cross-validation signal. Put the shape in
<code>shapes/&lt;category&gt;.ttl</code>. Prefer native SHACL core constraints &mdash;
<code>sh:minCount</code>, <code>sh:pattern</code>, <code>sh:or</code>, <code>sh:disjoint</code>,
property paths &mdash; where the check maps onto them cleanly; otherwise use <code>sh:sparql</code>
with a <code>sh:select</code> mirroring the <code>.rq</code> file's <code>WHERE</code> clause:</p>

<pre><code>{SHACL_EXAMPLE}</code></pre>

<p>Naming the shape exactly <code>oq:&lt;id&gt;</code> is all the tagging it needs. If one check
needs several node shapes, put <code>oq:checkId "&lt;id&gt;"</code> on each instead.</p>

<div class="note">
<span class="note-label">The severity trap</span>
<p>Declare <code>sh:severity</code> on the <em>shape node</em>, never inside the nested
<code>sh:sparql [ ... ]</code> block. SHACL defines it as a property of a shape; on the constraint it
parses cleanly, is honoured by some processors and ignored by others, and pyshacl silently
substitutes <code>sh:Violation</code>. The result is a check whose severity changes with
<code>--engine</code>. A test fails on both the misplacement and on a severity that disagrees with
the registry.</p>
</div>
</section>

<section class="band" id="add-native">
<h3>A native Python check</h3>
<p>Reach for this only when there is genuinely no single graph to pattern-match &mdash; OWL2 profile
membership, an external DL reasoner's verdict, comparing a graph against a <em>separate</em>
ontology's declarations, or diffing two ontology versions. The conformance family is the clearest
example: <code>CNF-001</code> through <code>CNF-005</code> cannot be SPARQL checks because they need
two graphs, one supplying the declarations and one supplying the usage.</p>
<ol class="steps">
<li><h4>Add the registry entry anyway</h4>
<p>Same fields as any other check. This is what lets the finding flow through the same report layer
as everything else. There is deliberately no separate native-check registry.</p></li>
<li><h4>Return <code>ResultRow</code> objects directly</h4>
<p>Construct them with your <code>check_id</code> and <code>category</code>, plus whatever
<code>focus_node</code>, <code>path</code>, <code>value</code>, <code>message</code>,
<code>remediation</code> and <code>sources</code> apply.
<code>conformance_to_rows</code> in <code>dataquality/data_quality.py</code> is the pattern to
copy.</p></li>
<li><h4>Wire it into a pipeline stage</h4>
<p>Append the rows to the relevant stage's <code>StageResult.rows</code>, then regenerate the
docs.</p></li>
</ol>
<div class="note">
<span class="note-label">If it walks a hierarchy, walk it iteratively</span>
<p>Depth is set by the input &mdash; the longest <code>rdfs:subClassOf</code> chain, or an RDF
collection's <code>rdf:rest</code> chain &mdash; and CPython's recursion limit is 1000 frames. Reuse
<code>ontology_suite/hierarchy.py</code> rather than writing another recursive walk. Neither
<code>sys.setrecursionlimit</code> (it trades a catchable error for a hard crash) nor a depth cap
(any value safe under the ceiling also truncates real answers) is a fix.</p>
</div>
</section>

<section class="band" id="own-panel">
<h2>Running your own panel</h2>
<p>You do not need a checkout of the suite, or to edit anything inside <code>site-packages</code>,
to change which checks run. <code>checks</code>, <code>data</code> and <code>run</code> each accept
<code>--registry</code>, <code>--shapes</code> and <code>--sparql</code>, independently overridable
and defaulting to the installed package's own resources.</p>

<pre><code># find the installed defaults and take a copy you can edit
python -c "from ontology_suite import config; print(config.PACKAGE_RESOURCES)"
cp -r &lt;the path printed above&gt; my-checks

# remove a check: delete its query -- discovery is a directory walk, not a manifest
rm my-checks/sparql/quality/QUA-004.rq

# add one: a registry entry plus a .rq file, exactly as above

# point the run at your copy
ontology-quality-suite checks --ontology domain.ttl \
  --registry my-checks/registry.json \
  --shapes   my-checks/shapes \
  --sparql   my-checks/sparql</code></pre>

<p>The three flags are independent, so a project can keep the stock shapes and SPARQL while
overriding only the registry &mdash; which is how you re-severitise a check for one project without
touching its logic. Change <code>default_severity</code> in your copy and the finding keeps its id,
prose and implementation while moving between <code>Violation</code>, <code>Warning</code> and
<code>Info</code>. Since <code>--fail-on</code> gates the exit code on severity, that is also how you
decide what breaks a build.</p>

<p>A minimal worked example ships in the repo: <code>examples/acme_robotics/custom_checks/</code>
holds a project-local registry with a single check, <code>ACM-001</code>, and its query &mdash; a
complete illustration of the smallest thing that works.</p>
</section>

<section class="band" id="pitfalls">
<h2>Pitfalls worth knowing</h2>

<h3>Vocabulary you do not declare yourself</h3>
<p>Checks that ask "is this term declared?" need to know which terms nobody declares locally.
Built-in RDF, RDFS and OWL2 vocabulary is assumed axiomatically &mdash; no ontology asserts
<code>owl:Restriction a owl:Class</code>. Omitting that exemption produced 149 false "undefined
class" findings against one vehicle ontology, one per anonymous restriction. The same applies to
annotation properties nobody re-declares: <code>rdfs:label</code>, <code>rdfs:comment</code> and the
SKOS lexical properties. A missing <code>skos:prefLabel</code> exemption once flooded every
SKOS-labelled taxonomy with false undeclared-property findings.</p>

<h3>Conventions that are not rdfs:domain and rdfs:range</h3>
<p>A structural check that hardcodes one modelling convention will fire on every ontology using a
different one. <code>STR-003</code> accepts gist-style <code>domainIncludes</code> and
<code>rangeIncludes</code> as satisfying "has a domain and range", matched by local name rather than
by a hardcoded namespace, since gist has published under more than one over the years.</p>

<h3>Findings that only make sense before inference</h3>
<p>Before adding a check to <code>sparql/logical/</code>, decide whether it survives the closure. If
it describes how axioms were authored rather than what they entail, it belongs outside
<code>closure-safe/</code>.</p>

<h3>Nested property shapes lose their check id</h3>
<p>pyshacl reports a property-constraint violation's <code>sh:sourceShape</code> as the nested
blank-node property shape, not the enclosing node shape carrying the <code>oq:checkId</code>
annotation. Id resolution walks up via <code>sh:property</code> to compensate. Before it did, 435 of
894 findings against a real ontology arrived with no check id, category or remediation &mdash; every
native SHACL-core finding in the suite.</p>
</section>

<footer>
<p>Generated from <code>ontology_suite/resources/registry.json</code>, the SPARQL tree under
<code>resources/sparql/</code> and the shapes under <code>resources/shapes/</code>. Counts, severities
and prose are read from those files rather than restated, so this page and the suite cannot disagree.
See <code>docs/CHECKS.md</code> for the generated per-check reference, <code>docs/EXTENDING.md</code>
for the authoring guide and <code>docs/ARCHITECTURE.md</code> for how the stages fit together.</p>
</footer>

</main>
</div>
</div>
"""

def main() -> None:
    OUT_PATH.write_text(DOC, encoding="utf-8")
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)} -- {len(checks)} checks, {len(DOC):,} bytes")


if __name__ == "__main__":
    main()
