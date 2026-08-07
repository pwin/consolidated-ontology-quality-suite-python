#!/usr/bin/env python3
"""
build_documentation.py

Injects a JSON data model (produced by extract_ontology_data.py) into
the HTML documentation template, replacing the __ONTOLOGY_DATA_JSON__
placeholder, and writes out a single self-contained HTML file.

The template itself does the actual documentation rendering in-browser,
using Handlebars.js (from cdnjs) for the reference tables and Mermaid.js
(from cdnjs) for the class-hierarchy diagrams - see
templates/documentation-template.html for that logic. This script's job
is only the data-injection step, kept separate from extraction so you
can re-run just this step (e.g. after hand-editing the template's CSS
or prose) without re-parsing the Turtle source.

Usage:
    python3 build_documentation.py \\
        --data ontology_doc_data.json \\
        --template ../templates/documentation-template.html \\
        --out ../output/vehicle-ontology-documentation.html
"""

import argparse
import json
import sys
from pathlib import Path

PLACEHOLDER = "__ONTOLOGY_DATA_JSON__"


def build(data_path, template_path, out_path):
    data_json = Path(data_path).read_text()

    # Fail fast and clearly if the extracted JSON isn't valid, rather
    # than embedding broken JSON into the page and finding out in the
    # browser console.
    try:
        parsed = json.loads(data_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: {data_path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if "</script" in data_json:
        # Would prematurely close the embedding <script> tag in the
        # template. Escape defensively rather than assume it can't happen.
        data_json = data_json.replace("</script", "<\\/script")

    html = Path(template_path).read_text()
    if PLACEHOLDER not in html:
        print(f"ERROR: placeholder {PLACEHOLDER} not found in {template_path}. "
              f"Has the template been edited to remove it?", file=sys.stderr)
        sys.exit(1)

    html = html.replace(PLACEHOLDER, data_json)

    Path(out_path).write_text(html)
    print(f"Wrote {out_path} "
          f"({len(parsed.get('classes', []))} classes, "
          f"{len(parsed.get('objectProperties', []))} object properties, "
          f"{len(parsed.get('datatypeProperties', []))} datatype properties, "
          f"{len(parsed.get('externalReuse', []))} external terms).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, help="Path to ontology_doc_data.json (from extract_ontology_data.py)")
    parser.add_argument("--template", required=True, help="Path to documentation-template.html")
    parser.add_argument("--out", required=True, help="Output path for the final HTML file")
    args = parser.parse_args(argv)
    build(args.data, args.template, args.out)


if __name__ == "__main__":
    main()
