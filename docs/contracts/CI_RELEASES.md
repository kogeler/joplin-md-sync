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
`make ci`, separately test every runtime and platform in the supported CI
matrix, run Linux installer tests, run the complete live protocol suite on
Linux AMD64 against the checksum-verified ephemeral Joplin Desktop build owned
by the live-test runtime, and build and smoke each supported native
distribution. Coverage MUST remain blocking at the reviewed floor. The live
job MUST NOT use a repository credential or a pre-existing Joplin process or
profile.

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
boundary, run only when a pull request changes `CHANGELOG.md`, grant
pull-request write only to its job, execute only trusted default-branch code,
read a bounded head changelog through the API as inert data, and mirror the
newest populated section, normally `Unreleased`, without requiring a version
change. It MUST replace only its single marker-delimited block, preserve manual
body content, refuse a concurrently changed body, and reject malformed markers,
missing entries, or oversized content.

**Evidence:**

- [`test_pr_body_is_the_only_pull_request_target_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pr_body_is_the_only_pull_request_target_write_boundary`
- [`test_prefers_populated_unreleased_without_a_version_change`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_prefers_populated_unreleased_without_a_version_change`
- [`test_skips_empty_unreleased_and_preserves_manual_body`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_skips_empty_unreleased_and_preserves_manual_body`
- [`test_rejects_missing_release_entries`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_rejects_missing_release_entries`
- [`test_rejects_malformed_or_reserved_markers`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_rejects_malformed_or_reserved_markers`
- [`test_rejects_an_oversized_existing_body`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_rejects_an_oversized_existing_body`

### `CIR-005` - Release version has one human-maintained owner

**Contract:** Root `.version` MUST be canonical stable SemVer and the only
human-maintained version. Package metadata MUST read it dynamically and the
agent manifest MUST match. An ordinary change MAY keep a current version that
is already published as a non-draft, non-prerelease GitHub Release and record
its notes under `CHANGELOG.md` `## [Unreleased]`. A release-bearing change MUST
increase the exact base version, a change that keeps a not yet published base
version MUST exceed the latest published release, and the release MUST provide
a matching non-empty dated changelog section.

**Evidence:**

- [`test_cli_accepts_an_unchanged_published_version_only`](../../tests/unit/test_version_increment.py) - `tests/unit/test_version_increment.py::VersionIncrementTest::test_cli_accepts_an_unchanged_published_version_only`
- [`test_cli_accepts_an_explicit_older_base_version`](../../tests/unit/test_version_increment.py) - `tests/unit/test_version_increment.py::VersionIncrementTest::test_cli_accepts_an_explicit_older_base_version`
- [`test_version_file_is_single_source`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_version_file_is_single_source`
- [`test_manifest_matches_version_file`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_manifest_matches_version_file`
- [`test_pyproject_reads_version_dynamically`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_pyproject_reads_version_dynamically`
- [`test_version_job_compares_exact_base_and_head`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_version_job_compares_exact_base_and_head`
- [`test_version_job_accepts_published_maintenance_and_requires_other_increases`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_version_job_accepts_published_maintenance_and_requires_other_increases`

### `CIR-006` - Publication reuses gated artifacts and never rewrites conflict

**Contract:** A version whose GitHub Release is not yet published MUST pass
reusable CI on main before its Python distributions are reproducibly built
once, install-smoked, and published. Release need MUST come from exact
external publication state, not from changed paths or a `.version` diff: once
the GitHub Release is published, every later main push MUST skip reusable CI
and all publication jobs, and the published release MUST be matched at its own
tag even after main has advanced. The GitHub publish job MUST publish first: it
MUST create or resume an exact draft Release, combine the shared Python
distributions with the smoke-tested platform artifacts, verify the full
inventory and checksums, upload each exact asset through GitHub's release
upload endpoint with response verification, and publish. An unpublished draft
left by an earlier attempt at another commit MUST be replaced rather than block
the release. Conflicting tags, published targets, metadata, or assets MUST fail
without moving or replacing history.

**Evidence:**

- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`
- [`test_published_release_makes_later_main_pushes_a_no_op`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_published_release_makes_later_main_pushes_a_no_op`
- [`test_requires_all_standalones_when_requested`](../../tests/unit/test_standalone.py) - `tests/unit/test_standalone.py::StandaloneInventoryTest::test_requires_all_standalones_when_requested`
- [`test_uses_only_the_exact_current_changelog_section`](../../tests/unit/test_release_notes.py) - `tests/unit/test_release_notes.py::test_uses_only_the_exact_current_changelog_section`

### `CIR-007` - Dependency submission is trusted-main-only

**Contract:** Dependency submission MUST trigger only on direct main push,
build four validated lock manifests offline from the locks and their
requirements inputs, validate the expected repository
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

**Contract:** Because PyPI files are immutable, PyPI MUST be published only
after the GitHub Release of the same run succeeds, with the same gated wheel
and sdist workflow artifact, through the `pypi` GitHub Environment, PyPI
Trusted Publishing, a job-scoped OIDC token, and the SHA-pinned official PyPA
action; stored credentials and the publisher's blind duplicate skipping are
forbidden. Release state MUST inspect the exact current version in PyPI before
that step: an existing PyPI version MUST be kept and its publication skipped,
and a missing version MUST be published.

**Evidence:**

- [`test_pypi_publication_uses_oidc_and_verified_shared_artifacts`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pypi_publication_uses_oidc_and_verified_shared_artifacts`
- [`test_normalization_makes_equivalent_sdist_archives_byte_identical`](../../tests/unit/test_normalize_sdist.py) - `tests/unit/test_normalize_sdist.py::test_normalization_makes_equivalent_sdist_archives_byte_identical`
- [`test_pypi_readme_uses_only_portable_absolute_links`](../../tests/unit/test_pypi_metadata.py) - `tests/unit/test_pypi_metadata.py::test_pypi_readme_uses_only_portable_absolute_links`
- [`test_pypi_metadata_exposes_public_project_routes`](../../tests/unit/test_pypi_metadata.py) - `tests/unit/test_pypi_metadata.py::test_pypi_metadata_exposes_public_project_routes`

### `CIR-012` - Mutable pins have one executable owner

**Contract:** Concrete project, dependency, runtime, protocol, schema,
external-tool, action commit, and checksum values MUST be owned by the
executable configuration that consumes them. Tests MUST NOT compare behavior
or repository configuration with duplicated concrete version literals. They
MUST instead use the owning production constant, derive expectations from
synthetic inputs, or verify version-independent structure and relationships.
CI version increment and release consistency enforcement MUST remain blocking.

**Evidence:**

- [`test_tests_do_not_duplicate_owned_version_pins`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_tests_do_not_duplicate_owned_version_pins`
- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
- [`test_live_ci_uses_checksum_verified_ephemeral_joplin_binary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_live_ci_uses_checksum_verified_ephemeral_joplin_binary`
- [`test_version_job_compares_exact_base_and_head`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_version_job_compares_exact_base_and_head`
- [`test_direct_dependencies_are_exact_and_scoped`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_direct_dependencies_are_exact_and_scoped`
