<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Dependency Maintenance

Development dependencies use standard `pyproject.toml` groups and one native
`uv.lock`. The [dependency contract](../contracts/DEPENDENCIES.md) defines the
boundaries; uv handles resolution, platform markers, hashes, and lock validity.
The installed joplin-md-sync package still has no runtime dependencies.

## Install the tools

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and a
supported Python interpreter. `.uv-version` records the reviewed uv CLI version
used by CI's official setup-uv action. The project declares a compatible
minimum rather than requiring Dependabot's updater to use that exact binary.

| Make target | Group | Purpose |
| --- | --- | --- |
| `make venv` | none | Editable runtime package only |
| `make venv-dev` | `quality` | Ruff, mypy, Bandit, pip-audit |
| `make venv-test` | `test` | pytest, coverage, parallel tests |
| `make venv-package` | `build` | Distribution tools and build backend |
| `make venv-docs` | `docs` | MkDocs site |

Every environment stays under its host/user-specific `.venvs/<key>/` path.
Make passes that path through `UV_PROJECT_ENVIRONMENT` and selects only the
required group. A shared lock does not install every tool in every job.
Third-party tools use wheels; the release build uses its locked backend with
build isolation disabled, rather than resolving another backend version.
The uv cache also tracks `.version`, so a release version change refreshes
editable package metadata.

## Change dependencies

Add or remove a tool with the native manager, for example:

```bash
uv add --group test PACKAGE
uv remove --group test PACKAGE
```

Or edit the appropriate requirement in `pyproject.toml` and run `make lock`.
Commit the manifest and `uv.lock` together. Never hand-edit the lock.

```bash
make lock-check             # uv lock --check; no regeneration
make refresh-dependencies   # uv lock --upgrade within declared requirements
make check
make ci
make smoke
```

Requirements describe compatibility; the lock records exact installed versions.
New registry releases do not make an existing lock stale. CI installs with
`--locked`, so a missing/incompatible lock fails instead of being rewritten.
There is no second text-based lock comparison or unit-test lock parser.

## Dependabot

The root project uses `package-ecosystem: uv`. Dependabot updates direct and
transitive packages together in one Python version-update group, with weekly
checks, a seven-day version cooldown, one open version-update PR, and automatic
rebasing. Actions have their own group. GitHub security alerts and grouped
security updates remain enabled in repository settings; their queue is
separate from the ordinary version-update schedule.

Keep Dependabot as the only automatic writer of its branches. CI does not
repair locks or push commits. Merge application compatibility fixes through
normal PRs and let Dependabot refresh its branch afterward. Retire the old pip
update PR after this migration reaches main, then use the new uv updates;
do not manually translate an old bot branch to the new format.

GitHub provides native uv alerts and security updates; no repository snapshot
submission workflow is needed. After migration reaches main, check the
repository's dependency graph and Dependabot update logs to confirm the hosted
service sees the manifest, groups, and transitive locked versions. These are
hosted integration checks, not claims made by a local synthetic updater test.

## What CI checks

The native lock check and group installations run through Make. Application
tests, coverage, live Joplin protocols, Windows/Linux compatibility, native
distribution smoke tests, and strict documentation builds remain the acceptance
criteria. A real incompatible update stays blocked; harmless lock formatting
does not have an additional repository-specific validator.

`make audit` exports all groups from `uv.lock` into the standard PEP 751
`.artifacts/audit/pylock.toml` with `uv export --locked`, then uses pip-audit's
native `--locked` mode. This covers every locked platform variant, including
Windows-only packages, rather than filtering a requirements file for the
current Linux host. The export is ignored by Git and is never maintained
independently. Vulnerability findings remain blocking; the CI dependency-review
job is retained as well.

See the official [uv/Dependabot integration](https://docs.astral.sh/uv/guides/integration/dependabot/)
and [locking and syncing guide](https://docs.astral.sh/uv/concepts/projects/sync/).
