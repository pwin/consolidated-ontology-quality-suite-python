"""Lets `python -m ontology_suite ...` work as an alternative to the
installed `ontology-quality-suite` console script -- same CLI, same
behavior; useful wherever the console script isn't on PATH."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
