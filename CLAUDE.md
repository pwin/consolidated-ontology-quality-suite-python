# Working in this repository

## Branching: `main` only

`main` is the only long-lived branch. There is no `develop`; it was retired on
2026-10-23 and should not be recreated.

Why it was retired, because the failure is easy to repeat: releases were being
tagged on `develop` while `main` stayed behind. By 0.24.3, `main` sat four
commits back at 0.24.1 and contained neither the `v0.24.2` nor the `v0.24.3`
tag, so anyone cloning the repository and reading the default branch saw
`holosdb>=0.15` while PyPI served a build declaring `>=0.24`. That is the same
failure the `0.24.2` commit message warns about one level up: *a packaging fix
that is not released is worse than one not attempted, because the next person
reads the source and believes it.*

Work directly on `main` for small changes. For anything you want reviewed, use
a short-lived branch and squash-merge it.

## Commits: squash

The repository allows squash merges only; merge commits and rebase merges are
disabled, and branches are deleted on merge. A pull request lands as one
commit, titled from the PR title with the PR body as its message.

Write the reasoning into the commit message. The history here is used as an
explanation of why the code is as it is -- several commit messages are cited
by name from `docs/` and from the SemOps Manual's evidence -- so a squashed
commit should still say what moved and what was measured, not just what
changed.

## Releases

1. Bump `version` in `pyproject.toml`.
2. Run `uv lock`. The lock records the project's own version, so it moves in
   the same commit. `.github/workflows/lock.yml` fails the build otherwise.
3. Commit on `main`, then push an annotated tag `vX.Y.Z`.

The tag triggers `.github/workflows/publish.yml`, which builds, publishes to
TestPyPI, smoke-tests an install from there, publishes to PyPI, and creates the
GitHub Release from the tag annotation. Nothing publishes without a tag.

Two rules learned the hard way:

- **A dependency floor change does nothing until it is released.** Raising
  `holosdb>=0.23` in `pyproject.toml` left the published 0.24.1 metadata still
  saying `>=0.15`, so every `pip install` kept resolving the old floor while the
  repository read as fixed. Check the published metadata after releasing one.
- **Publish a dependency before pushing a bound that needs it.** 0.23.0's
  `shacl>=0.3.3` landed six hours before shacl 0.3.3 existed, and broke
  resolution on every branch until it did.

## Severity

A check's severity is declared twice and the two must agree: `sh:resultSeverity`
in its `.rq` / `sh:severity` in its `.ttl`, and `default_severity` in
`registry.json`. The check's copy drives behaviour and `--fail-on`; the
registry's drives report badges and cucumber tags. Nothing validates the
agreement at runtime -- `tests/test_shacl_native_runner.py` does it at test
time. Set severity to what is true and use `--fail-on` to decide what blocks a
build; do not relabel a finding to get a green build.

See `docs/EXTENDING.md` for adding checks, and
`examples/riverside_library/README.md` for a worked example of adding
project-local ones.
