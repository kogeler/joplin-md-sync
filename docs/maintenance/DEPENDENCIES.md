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

Dependabot reads every `.in` file as a native pip-compile manifest and updates
its matching `.txt` lock. A common `allow: dependency-type: all` rule includes
every direct and transitive Python dependency, including packages shared by
several audience locks. Python version updates run daily and form one group
across all four audiences, with a limit of one open version-update PR. GitHub
Actions version updates run weekly in their own group and touch separate files.
`pyproject.toml` is excluded from its pip manifests because it carries no tool
dependency versions; Dependabot has never managed its build-backend pin, so
update that pin manually together with the `setuptools` entry of the
`LOCK_BOOTSTRAP` resolver. GitHub Actions updates are grouped into a separate pull request.

The `LOCK_BOOTSTRAP` resolver in the Makefile pins the same pip and pip-tools
pair as Dependabot's pip-compile updater, which is listed in
[`python/helpers/requirements.txt`](https://github.com/dependabot/dependabot-core/blob/main/python/helpers/requirements.txt)
of `dependabot-core`. When Dependabot moves to a newer pair, update the
bootstrap and reproduce all four locks in the same change.

### Security updates and branch ownership

Enable Dependabot alerts, Dependabot security updates, and grouped security
updates in the repository's security settings. These settings are external to
the YAML configuration. The configuration also explicitly groups all Python
security updates together, and all Actions security updates together, so
grouping remains reviewable with the code.

GitHub has separate version and security update queues. The one-PR version
limit does not limit security PRs, and GitHub does not combine a security group
with a version group. Both queues use the same bot and automatic rebasing.
After a security PR merges, let Dependabot refresh the version PR and wait for
CI on its new commits. Changes to the same locks can still require rebasing;
grouping is not a guarantee against every merge conflict.

Keep Dependabot as the sole automatic writer of update branches. CI checks
their committed content without running a repair-and-push workflow. Avoid
manual commits in bot branches, which interfere with automatic branch updates.
Make any application compatibility changes in a normal PR. For a manual
dependency update, regenerate the complete affected set from current `main`
and let the bot refresh its outstanding PR afterward. An incompatible graph
or failed application test leaves the update blocked for review; merging
remains a maintainer action.

### Executable acceptance

Run `make test-dependency-updates` on Linux AMD64 with Podman or Docker. It
uses the upstream pip-updater image pinned by `DEPENDABOT_IMAGE` in the
Makefile; refresh that digest deliberately when upgrading the acceptance
runtime, and run the same tests before accepting it. The first run downloads
the image and bootstraps the locked environments. Test scenarios then run
with networking disabled, synthetic local wheels, and no GitHub credentials.

The tests pass the repository's actual allow and group rules into Dependabot,
exercise its parser, requirements updater, and file updater, and verify:

- Direct input and lock updates occur together.
- Version and security updates include transitive packages in every audience
  that uses them, including dependencies more than one level deep.
- Regeneration agrees with `make lock` and `make freeze-check`, and repeating
  an update does not create more changes.
- Hash-verified installation into fresh environments executes the updated
  dependency chain.
- An incompatible audience rejects the proposed update without rewriting the
  original files.

These tests cover eligibility, grouping, and file generation. GitHub's hosted
scheduler, actual PR creation, and rebasing are observable only after the
configuration reaches the default branch. The normal application, live Joplin,
platform, distribution, documentation, and vulnerability gates still assess
each real update in CI.

Dependabot pull requests keep `.version` unchanged and run the same drift,
platform, audit, and snapshot gates as a manual update.
