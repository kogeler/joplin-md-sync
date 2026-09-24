# Contributing

## Ground rules

- Read the affected assertion and evidence in `docs/contracts/` before changing
  behavior. Planner and executor changes also require
  `docs/maintenance/SYNCHRONIZATION.md`.
- A runtime dependency first requires the policy described by `DEP-001` in
  `docs/contracts/DEPENDENCIES.md`.
- Every observable contract change updates the stable assertion, its evidence,
  and the `## [Unreleased]` changelog section.

## Development setup

Purpose-specific virtual environments are managed by the Makefile (CI runs the
exact same targets). Each lives under `.venvs/<key>/`, where the key is private
to the current machine and user, so one checkout on a shared or network drive
works from several hosts:

```bash
make venv         # .venvs/<key>/venv/         - the package installed editable
make venv-dev     # .venvs/<key>/venv-dev/     - Linux quality tools: ruff, mypy, audits
make venv-test    # .venvs/<key>/venv-test/    - cross-platform test tools
make venv-package # .venvs/<key>/venv-package/ - cross-platform packaging tools
make check        # lint + typecheck + full test suite
make freeze-check # verify all generated locks are reproducible
make ci           # complete Linux CI contract, including docs and security
make package      # dist/: wheel, sdist, joplin-md-sync.pyz, SHA256SUMS.txt
make smoke        # clean-venv install of the built wheel + CLI smoke tests
make clean        # remove this host's environments and generated artifacts
make help         # list all targets
```

The test suite needs no real Joplin: `tests/fake_joplin_server` fakes the
Data API.

## Dependency policy

The normative policy is the
[dependency contract](docs/contracts/DEPENDENCIES.md). Direct pins belong in
the matching `requirements-*.in` file; run `make lock` and commit every
generated `requirements-*.txt` change together. Never hand-edit a lock. Follow
[Dependency maintenance](docs/maintenance/DEPENDENCIES.md) for the update
procedure.

## Versioning

The single source of the version is the root `.version` file:
`pyproject.toml` reads it dynamically, the runtime resolves it from the
package/checkout/metadata, and `make verify-release` enforces that
`agent-manifest.json` matches.

## Pull requests

- One logical change per PR; add or extend tests for observable behavior
  (not implementation details).
- `make check` and `make verify-release` must pass on Python 3.13 and 3.14.
- `make test-live` is an explicit local acceptance target for MCP and GPT
  Actions changes. It reads the ignored `./token` file and is intentionally
  excluded from CI.
- Add release-worthy changes to the existing `## [Unreleased]` section of
  `CHANGELOG.md` and keep `.version` unchanged. Changing the changelog
  refreshes a managed block in the PR description; write your own text outside
  it. Only deliberate release preparation advances `.version`; see
  [Releases](docs/maintenance/RELEASES.md).

## Release process

See [Releases](docs/maintenance/RELEASES.md).
