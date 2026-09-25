<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Dependency Maintenance

The [dependency contract](../contracts/DEPENDENCIES.md) owns all requirements
for manifests, direct pins, generated locks, platform audiences, auditing, and
GitHub dependency submission.

## Model

Four `requirements-*.in` files hold every exact direct Python pin. Each is
compiled by pip-compile into a same-stem generated hash lock and serves one
independently installable audience:

| Audience | Direct input | Generated lock | Used for |
| --- | --- | --- | --- |
| Quality | `requirements-dev.in` | `requirements-dev.txt` | Ruff, mypy, Bandit, audit, workflow checks |
| Tests | `requirements-test.in` | `requirements-test.txt` | pytest, coverage, parallel test execution |
| Packaging | `requirements-package.in` | `requirements-package.txt` | wheel, sdist, zipapp, native executable |
| Documentation | `requirements-docs.in` | `requirements-docs.txt` | strict MkDocs site build |

`pyproject.toml` holds package metadata and the exact `setuptools` build-backend
pin in `[build-system]`, which is maintained by hand. Runtime dependencies are
currently empty, so there is no empty `requirements.in` or `requirements.txt`,
and the published package exposes no internal tool extras.

The audience split is intentional. For example, Windows compatibility jobs do
not need a Linux quality tool merely because both are developer dependencies.
This allows direct tools to remain current without making an unrelated
platform responsible for every transitive wheel.

## Updating

1. Update exact direct pins in the matching `requirements-*.in` file to
   reviewed current stable releases.
2. Run `make lock` for that direct change, or `make refresh-dependencies` for
   a reviewed whole-graph upgrade, with the supported lock Python.
3. Inspect direct and transitive changes in all four generated locks.
4. Run `make freeze-check`, `make lock-platform-check`, `make audit`, and
   `make dependency-snapshot`.
5. Run `make ci` before submission.

Generated lock files are never edited by hand. `make freeze-check` recompiles
each input constrained by its committed lock, so it reports only a mismatch
between an input and its lock, not newer upstream releases. If a runtime
dependency is introduced, first define and test its installation, lock, audit,
package, and submission policy in
[`DEP-001`](../contracts/DEPENDENCIES.md#dep-001-requirements-inputs-own-every-direct-python-dependency).

## Dependabot

Dependabot reads every `.in` file as a native pip-compile manifest, updates its
matching `.txt` lock, and groups all Python changes into one pull request.
`pyproject.toml` is excluded from its pip manifests because it carries no tool
dependency versions; Dependabot has never managed its build-backend pin, so
update that pin manually together with the `setuptools` entry of the
`LOCK_BOOTSTRAP` resolver. GitHub Actions updates are grouped into a separate pull request.

The `LOCK_BOOTSTRAP` resolver in the Makefile pins the same pip and pip-tools
pair as Dependabot's pip-compile updater, which is listed in
[`python/helpers/requirements.txt`](https://github.com/dependabot/dependabot-core/blob/main/python/helpers/requirements.txt)
of `dependabot-core`. When Dependabot moves to a newer pair, update the
bootstrap and reproduce all four locks in the same change.

Dependabot pull requests keep `.version` unchanged and run the same drift,
platform, audit, and snapshot gates as a manual update.
