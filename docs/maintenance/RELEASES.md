<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Releases

Release guarantees are defined by
[`CIR-005` through `CIR-008`](../contracts/CI_RELEASES.md) and the
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
consumed by the release workflow.

## Review artifacts

Before tagging, inspect `dist/` and `dist/SHA256SUMS.txt`. The source archive,
wheel, zipapp, and native executable must report the intended version and pass
their smoke checks. Never edit generated checksums or release notes.

## Publish

Merge or push the reviewed release commit to `main`. Do not create or move the
version tag manually. The main-branch release workflow checks whether the exact
version is already published, reuses the CI gate, builds the remaining
artifacts, and creates the `vX.Y.Z` tag through the exact-target GitHub Release.

A matching published release is a no-op. A recoverable matching draft may be
completed; conflicting tags, release metadata, targets, or published assets
stop the workflow for inspection. Confirm the release body, complete asset
inventory, and checksums after publication. The documentation site is deployed
separately from the default branch by the Pages workflow.

## Post-release

Run the documented install and update-check commands against the published
release, then verify that the headless installer resolves the new stable tag
and checksum metadata. Record any corrective change as a new version; never
replace an already published tag or asset.
