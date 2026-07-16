# Release Process

Single source of version truth: the **`.version` file at the repository
root**. `pyproject.toml` reads it via setuptools dynamic metadata, the
package resolves `__version__` from it at runtime (embedded copy in the
zipapp, repo file in a checkout, distribution metadata in a wheel), and
`agent-manifest.json` must match — `make verify-release` enforces all of
this in CI.

## Steps

1. Update `.version`, the `version` field in `agent-manifest.json`, and
   `CHANGELOG.md` (move Unreleased → new version with the date).
2. Locally: `make check && make package && make verify-release`.
3. Commit, then tag and push:

   ```bash
   git tag v1.0.0
   git push origin main v1.0.0
   ```

4. The `release.yml` workflow (trigger: tags `v*`) reuses the same Makefile
   targets: verifies the tag matches `.version`
   (`make verify-release TAG=…`), reruns `make check` on Linux and Windows,
   builds all artifacts with checksums (`make package`), smoke-tests them
   (`make smoke`), and creates/updates the GitHub release with the assets.
5. Post-release smoke check from a clean machine:

   ```bash
   python -m pip install "git+https://github.com/kogeler/joplin-md-sync.git@v1.0.0"
   joplin-md-sync version --json
   joplin-md-sync update-check --json   # expect exit 0
   ```

PyPI publication is intentionally not configured; add a separate,
explicitly reviewed workflow if it is ever wanted.

## Compatibility rules

- Exit codes, JSON envelope keys, header schema, and state schema are
  stable within a major version.
- A state-schema bump ships a migration in `state.MIGRATIONS` and a minor
  version bump at minimum.
- Prereleases use tags like `v1.1.0-rc1` (ignored by `update-check`
  without `--include-prerelease`).
