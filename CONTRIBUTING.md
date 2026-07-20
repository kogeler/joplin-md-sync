# Contributing

## Ground rules

- Correctness over features: no silent overwrites, no unverified writes,
  deterministic output. Read `docs/STATE_MODEL.md` before touching the
  planner or executor.
- Zero third-party **runtime** dependencies. A new runtime dependency needs
  an ADR in `docs/` justifying it against the criteria in the README.
- Public contracts (exit codes, JSON envelope fields, the metadata header,
  the state schema) are versioned; breaking them requires a major release.

## Development setup

Two separate local virtual environments, both managed by the Makefile
(CI runs the exact same targets):

```bash
make venv        # venv/      — runtime: the package installed editable
make venv-dev    # venv-dev/  — tooling: ruff, mypy, build (pinned lock)
make check       # lint + typecheck + full test suite
make package     # dist/: wheel, sdist, joplin-md-sync.pyz, SHA256SUMS.txt
make smoke       # clean-venv install of the built wheel + CLI smoke tests
make help        # list all targets
```

The test suite needs no real Joplin: `tests/fake_joplin_server` fakes the
Data API.

## Dependency policy

- Runtime dependencies are declared in `pyproject.toml` (`dependencies`) —
  currently empty **by design**; adding one needs an ADR (see README).
- Development tools are declared unpinned in `[dependency-groups]` in
  `pyproject.toml`; the committed `requirements-dev.txt` is a full
  `pip freeze` lock of `venv-dev/`. To upgrade tools, run `make freeze`
  and commit the refreshed lock.

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
- Update `CHANGELOG.md` under an "Unreleased" heading.

## Release process

See `docs/RELEASE_PROCESS.md`.
