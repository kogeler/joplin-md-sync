<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Continuous Integration And Release Contract

## Assertions

### `CIR-001` - Workflow topology is event-driven and immutable

**Contract:** The repository MUST contain only the reviewed CI,
dependency-submission, Pages, PR-body, and release workflows. External actions
MUST use full commit SHAs, validator images MUST use immutable digests,
checkout credentials MUST not persist, and every workflow MUST declare
permissions and concurrency behavior.

**Evidence:**

- [`test_workflow_set_is_event_driven_and_every_action_is_sha_pinned`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_workflow_set_is_event_driven_and_every_action_is_sha_pinned`

### `CIR-002` - Quality, compatibility, and distribution remain visible gates

**Contract:** Reusable CI MUST run the complete Linux quality contract through
`make ci`, separately test supported Python 3.13/3.14 and Linux AMD64/ARM64 and
Windows AMD64 compatibility, run Linux installer tests, run the complete live
protocol suite on Linux AMD64 against a checksum-verified ephemeral Joplin
Desktop 3.6.15 profile, and build and smoke each supported native distribution.
Coverage MUST remain blocking at the reviewed floor. The live job MUST NOT use
a repository credential or a pre-existing Joplin process or profile.

**Evidence:**

- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
- [`test_live_ci_uses_checksum_verified_ephemeral_joplin_binary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_live_ci_uses_checksum_verified_ephemeral_joplin_binary`
- [`test_supported_platform_names`](../../tests/unit/test_standalone.py) - `tests/unit/test_standalone.py::StandaloneNameTest::test_supported_platform_names`

### `CIR-003` - Write permissions are confined to dedicated jobs

**Contract:** Default workflow permission MUST be read-only contents. Only the
CodeQL job MAY receive `security-events: write`, dependency submission MAY
receive job-scoped `contents: write`, Pages deployment MAY receive job-scoped
Pages and OIDC writes, PR-body automation MAY receive pull-request write, and
GitHub release publication MAY receive job-scoped contents write. PyPI
publication MAY receive only job-scoped OIDC write and MUST NOT receive a
stored credential.

**Evidence:**

- [`test_dependency_submission_is_a_separate_trusted_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_dependency_submission_is_a_separate_trusted_write_boundary`
- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`
- [`test_pages_validates_prs_and_confines_publish_permissions`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pages_validates_prs_and_confines_publish_permissions`
- [`test_pr_body_is_the_only_pull_request_target_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pr_body_is_the_only_pull_request_target_write_boundary`
- [`test_pypi_publication_uses_oidc_and_verified_shared_artifacts`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pypi_publication_uses_oidc_and_verified_shared_artifacts`

### `CIR-004` - PR-body automation treats head content as data

**Contract:** PR-body automation MUST be the sole `pull_request_target`
boundary, execute only trusted default-branch code, read a bounded head
changelog through the API as inert data, preserve manual body content, and
reject missing or oversized managed release content.

**Evidence:**

- [`test_pr_body_is_the_only_pull_request_target_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pr_body_is_the_only_pull_request_target_write_boundary`
- [`test_skips_empty_unreleased_and_preserves_manual_body`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_skips_empty_unreleased_and_preserves_manual_body`
- [`test_rejects_missing_release_entries`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_rejects_missing_release_entries`

### `CIR-005` - Release metadata is verified only at publication

**Contract:** Root `.version` MUST be canonical stable SemVer and the only
human-maintained version. Package metadata MUST read it dynamically and the
agent manifest MUST match. Ordinary tests MUST NOT compare the current project
version with a base revision or duplicate mutable project, dependency, tool,
runtime, or action version values. Reusable CI MUST NOT require a project
version increment or run release metadata verification. Synthetic version
fixtures and public protocol or schema version assertions MAY be used when the
version itself is product behavior. Release metadata, tags, artifact versions,
and publication state MUST be validated by the dedicated release workflow
before publication.

**Evidence:**

- [`test_ci_leaves_release_version_policy_to_release_workflow`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_leaves_release_version_policy_to_release_workflow`
- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`

### `CIR-006` - Publication reuses gated artifacts and never rewrites conflict

**Contract:** A not-yet-published version on main MUST pass reusable CI before
its Python distributions are reproducibly built once, install-smoked, and its
publication jobs run. The GitHub publish job MUST create or resume an exact
draft Release, combine the shared Python distributions with the smoke-tested
platform artifacts, verify the full inventory and checksums, upload each exact
asset through GitHub's release upload endpoint with response verification, and
publish. A later main commit that retains a version already published in both
destinations MUST be a read-only no-op and MUST NOT retarget its release tag.
When publication is still required, conflicting tags, targets, metadata, or
assets MUST fail without moving or replacing history.

**Evidence:**

- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`
- [`test_published_version_is_a_noop_on_later_main_commits`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_published_version_is_a_noop_on_later_main_commits`
- [`test_requires_all_standalones_when_requested`](../../tests/unit/test_standalone.py) - `tests/unit/test_standalone.py::StandaloneInventoryTest::test_requires_all_standalones_when_requested`
- [`test_uses_only_the_exact_current_changelog_section`](../../tests/unit/test_release_notes.py) - `tests/unit/test_release_notes.py::test_uses_only_the_exact_current_changelog_section`

### `CIR-007` - Dependency submission is trusted-main-only

**Contract:** Dependency submission MUST trigger only on direct main push,
build four validated lock manifests offline, validate the expected repository
and payload shape, and submit with only its job-scoped standard token. Pull
requests and reusable release invocation MUST not enter this write boundary.

**Evidence:**

- [`test_dependency_submission_is_a_separate_trusted_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_dependency_submission_is_a_separate_trusted_write_boundary`
- [`test_builds_all_exact_lock_manifests`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_builds_all_exact_lock_manifests`

### `CIR-008` - Documentation changes build before merge and publish a sitemap

**Contract:** Documentation, theme, hook, and docs dependency changes MUST
trigger a strict non-deploying Pages build on pull requests. Direct main pushes
MUST build the same content, require non-empty sitemap XML and gzip outputs,
audit generated routes, links, anchors, canonical URLs, assets, and the
allow-all `robots.txt` sitemap directive, publish the canonical domain root
files, and grant deployment writes only to the deploy job.

**Evidence:**

- [`test_pages_validates_prs_and_confines_publish_permissions`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pages_validates_prs_and_confines_publish_permissions`
- [`test_documentation_tree_and_site_navigation_are_complete`](../../tests/unit/test_documentation_contracts.py) - `tests/unit/test_documentation_contracts.py::test_documentation_tree_and_site_navigation_are_complete`
- [`test_generated_site_audit_accepts_complete_output`](../../tests/unit/test_docs_site_audit.py) - `tests/unit/test_docs_site_audit.py::test_generated_site_audit_accepts_complete_output`

### `CIR-009` - Contract evidence is machine-checked

**Contract:** Contract files MUST be the only normative documentation, use
unique stable IDs and the catalogued assertion shape, and link every assertion
to at least one existing test definition. The user, maintenance, and site-only
trees MUST remain structurally separate and all published internal links MUST
resolve in a strict MkDocs build.

**Evidence:**

- [`test_contract_assertions_have_unique_ids_and_real_evidence`](../../tests/unit/test_documentation_contracts.py) - `tests/unit/test_documentation_contracts.py::test_contract_assertions_have_unique_ids_and_real_evidence`
- [`test_documentation_tree_and_site_navigation_are_complete`](../../tests/unit/test_documentation_contracts.py) - `tests/unit/test_documentation_contracts.py::test_documentation_tree_and_site_navigation_are_complete`
- [`test_all_relative_documentation_links_and_home_routes_resolve`](../../tests/unit/test_documentation_contracts.py) - `tests/unit/test_documentation_contracts.py::test_all_relative_documentation_links_and_home_routes_resolve`

### `CIR-010` - Repository ownership is explicit

**Contract:** `.github/CODEOWNERS` MUST assign every repository path to
`@kogeler`, so the default-branch ruleset's required code-owner review applies
to every pull-request change.

**Evidence:**

- [`test_codeowners_assigns_entire_repository_to_maintainer`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_codeowners_assigns_entire_repository_to_maintainer`

### `CIR-011` - PyPI publication is secretless, exact, and recoverable

**Contract:** Release state MUST independently inspect the exact current
version in PyPI and GitHub Releases. A missing PyPI version MUST publish only
the gated wheel and sdist through the `pypi` GitHub Environment, PyPI Trusted
Publishing, a job-scoped OIDC token, and the SHA-pinned official PyPA action;
stored credentials and blind duplicate skipping are forbidden. GitHub MUST
receive the same workflow artifact only after PyPI succeeds. If PyPI already
contains the version while GitHub publication still needs recovery, the local
file names, sizes, and SHA-256 digests MUST exactly match PyPI before GitHub
publication continues.

**Evidence:**

- [`test_pypi_publication_uses_oidc_and_verified_shared_artifacts`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pypi_publication_uses_oidc_and_verified_shared_artifacts`
- [`test_accepts_exact_local_and_pypi_distribution_inventory`](../../tests/unit/test_pypi_release.py) - `tests/unit/test_pypi_release.py::test_accepts_exact_local_and_pypi_distribution_inventory`
- [`test_rejects_a_local_distribution_that_differs_from_pypi`](../../tests/unit/test_pypi_release.py) - `tests/unit/test_pypi_release.py::test_rejects_a_local_distribution_that_differs_from_pypi`
- [`test_rejects_an_unexpected_or_yanked_pypi_distribution`](../../tests/unit/test_pypi_release.py) - `tests/unit/test_pypi_release.py::test_rejects_an_unexpected_or_yanked_pypi_distribution`
- [`test_normalization_makes_equivalent_sdist_archives_byte_identical`](../../tests/unit/test_normalize_sdist.py) - `tests/unit/test_normalize_sdist.py::test_normalization_makes_equivalent_sdist_archives_byte_identical`
- [`test_pypi_readme_uses_only_portable_absolute_links`](../../tests/unit/test_pypi_metadata.py) - `tests/unit/test_pypi_metadata.py::test_pypi_readme_uses_only_portable_absolute_links`
- [`test_pypi_metadata_exposes_public_project_routes`](../../tests/unit/test_pypi_metadata.py) - `tests/unit/test_pypi_metadata.py::test_pypi_metadata_exposes_public_project_routes`
