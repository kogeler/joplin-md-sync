# Release Process

Single source of version truth: the **`.version` file at the repository
root**. `pyproject.toml` reads it via setuptools dynamic metadata, the
package resolves `__version__` from it at runtime (embedded copy in the
zipapp/standalone executable, repo file in a checkout, distribution metadata
in a wheel), and
`agent-manifest.json` must match — `make verify-release` enforces all of
this in CI.

## Steps

1. Update `.version`, the `version` field in `agent-manifest.json`, and
   `CHANGELOG.md` (move Unreleased → new version with the date).
2. Open a pull request. The `version increment` CI job compares `.version`
   with the pull request base and requires a strictly newer `X.Y.Z` version.
3. Locally: `make check && make package && make verify-release`.
4. Merge the pull request into `main`. Every merge is expected to carry a
   version increment; no release tag is created manually.
5. The `release.yml` workflow (trigger: pushes to `main`) reruns `make check`
   on Linux AMD64/ARM64 and Windows AMD64. Each runner builds, launches, and
   uploads its native executable. The release job builds the wheel, sdist,
   and zipapp, downloads only those smoke-tested executables, writes one
   `SHA256SUMS.txt`, verifies the complete inventory, creates the annotated
   `vX.Y.Z` tag from `.version`, and publishes all artifacts. A tag that
   already points to a different commit fails the workflow instead of
   replacing the existing release.
6. Post-release smoke check from a clean machine:

   ```bash
   python -m pip install "git+https://github.com/kogeler/joplin-md-sync.git@v1.5.0"
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
