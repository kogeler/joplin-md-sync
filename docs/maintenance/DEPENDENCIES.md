<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Dependency Maintenance

The [dependency contract](../contracts/DEPENDENCIES.md) owns all requirements
for manifests, direct pins, generated locks, platform audiences, auditing, and
GitHub dependency submission.

## Model

`pyproject.toml` is the direct dependency manifest. Runtime dependencies are
currently empty, so there is no empty `requirements.txt`. Four generated locks
serve four independently installable audiences:

| Audience | Lock | Used for |
| --- | --- | --- |
| Quality | `requirements-dev.txt` | Ruff, mypy, Bandit, audit, workflow checks |
| Tests | `requirements-test.txt` | pytest, coverage, parallel test execution |
| Packaging | `requirements-package.txt` | wheel, sdist, zipapp, native executable |
| Documentation | `requirements-docs.txt` | strict MkDocs site build |

The audience split is intentional. For example, Windows compatibility jobs do
not need a Linux quality tool merely because both are developer dependencies.
This allows direct tools to remain current without making an unrelated
platform responsible for every transitive wheel.

## Updating

1. Update exact direct pins in `pyproject.toml` to reviewed current stable
   releases.
2. Run `make refresh-dependencies` with the supported lock Python.
3. Inspect direct and transitive changes in all four generated locks.
4. Run `make freeze-check`, `make lock-platform-check`, `make audit`, and
   `make dependency-snapshot`.
5. Run `make ci` before submission.

Use `make lock` only to regenerate from unchanged direct pins. Generated lock
files are never edited by hand. If a runtime dependency is introduced, first
define and test its installation, lock, audit, package, and submission policy
in [`DEP-001`](../contracts/DEPENDENCIES.md#dep-001-pep-621-owns-every-direct-python-dependency).

Dependabot groups the direct Python pins and GitHub Actions updates. Its pull
requests still need the same lock regeneration and verification as manual
updates.
