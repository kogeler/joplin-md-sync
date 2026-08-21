<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Releases

Release guarantees are defined by
[`CIR-005` through `CIR-008` and `CIR-011`](../contracts/CI_RELEASES.md) and the
[dependency contract](../contracts/DEPENDENCIES.md). This page describes the
maintainer procedure without redefining those guarantees.

## Prepare

1. Update `.version` to the intended SemVer value.
2. Update `agent-manifest.json` to the same value.
3. Add the matching top entry to `CHANGELOG.md` with user-visible changes.
4. Update versioned installation examples when the release should become the
   documented stable version.
5. Refresh direct dependencies and locks when dependency updates are included.
6. Run `make ci`, `make package`, and `make smoke` on Linux.

`make verify-release` checks version ownership and changelog structure.
`make release-notes` extracts the current changelog entry into the artifact
consumed by the release workflow. The PyPI project trusts repository
`kogeler/joplin-md-sync`, workflow `release.yml`, and GitHub Environment
`pypi`; the environment stores no PyPI token because the publish job uses OIDC.

## Review artifacts

Before tagging, inspect `dist/` and `dist/SHA256SUMS.txt`. The source archive,
wheel, zipapp, and native executable must report the intended version and pass
their smoke checks. Never edit generated checksums or release notes.

## Publish

Merge or push the reviewed release commit to `main`. Do not create or move the
version tag manually. The main-branch release workflow checks whether the exact
version is already published independently in PyPI and GitHub Releases, reuses
the CI gate, builds wheel and sdist once, and publishes them through PyPI
Trusted Publishing. It then combines the same Python distributions with the
remaining artifacts and creates the `vX.Y.Z` tag through the exact-target
GitHub Release.

A matching publication in both destinations is a no-op. If only PyPI is
complete, the workflow verifies rebuilt distribution sizes and hashes against
PyPI before recovering GitHub publication. A recoverable matching GitHub draft
may be completed; conflicting package files, tags, release metadata, targets,
or published assets stop the workflow for inspection. Confirm the PyPI wheel,
sdist, and provenance plus the GitHub release body, complete asset inventory,
and checksums after publication. The documentation site is deployed separately
from the default branch by the Pages workflow.

## Post-release

Install the exact version from PyPI and run the documented version and
update-check commands, then verify that the headless installer resolves the new
stable tag and checksum metadata. Record any corrective change as a new
version; never replace a package file, tag, or release asset after publication.
