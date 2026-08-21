# Contributing

## Ground rules

- Read the affected assertion and evidence in `docs/contracts/` before changing
  behavior. Planner and executor changes also require
  `docs/maintenance/SYNCHRONIZATION.md`.
- A runtime dependency first requires the policy described by `DEP-001` in
  `docs/contracts/DEPENDENCIES.md`.
- Every observable contract change updates the stable assertion, its evidence,
  and the appropriate version and changelog entry.

## Development setup

Purpose-specific virtual environments are managed by the Makefile (CI runs the
exact same targets):

```bash
make venv        # venv/      — runtime: the package installed editable
make venv-dev    # venv-dev/  - Linux quality tools: ruff, mypy, audits
make venv-test   # venv-test/ - cross-platform test tools
make venv-package # venv-package/ - cross-platform packaging tools
make check       # lint + typecheck + full test suite
make freeze-check # verify all generated locks are reproducible
make ci          # complete Linux CI contract, including docs and security
make package     # dist/: wheel, sdist, joplin-md-sync.pyz, SHA256SUMS.txt
make smoke       # clean-venv install of the built wheel + CLI smoke tests
make help        # list all targets
```

The test suite needs no real Joplin: `tests/fake_joplin_server` fakes the
Data API.

## Dependency policy

The normative policy is the
[dependency contract](docs/contracts/DEPENDENCIES.md). Follow
[Dependency maintenance](docs/maintenance/DEPENDENCIES.md) for the update
procedure and commit all generated lock changes together.

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
- Increment `.version`, synchronize version mirrors and examples, and add the
  matching dated `CHANGELOG.md` section.

## Release process

See [Releases](docs/maintenance/RELEASES.md).
