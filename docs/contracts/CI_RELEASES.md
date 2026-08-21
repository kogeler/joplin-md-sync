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
Windows AMD64 compatibility, run Linux installer tests, and build and smoke
each supported native distribution. Coverage MUST remain blocking at the
reviewed floor.

**Evidence:**

- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
- [`test_supported_platform_names`](../../tests/unit/test_standalone.py) - `tests/unit/test_standalone.py::StandaloneNameTest::test_supported_platform_names`

### `CIR-003` - Write permissions are confined to dedicated jobs

**Contract:** Default workflow permission MUST be read-only contents. Only the
CodeQL job MAY receive `security-events: write`, dependency submission MAY
receive job-scoped `contents: write`, Pages deployment MAY receive job-scoped
Pages and OIDC writes, PR-body automation MAY receive pull-request write, and
release publication MAY receive job-scoped contents write.

**Evidence:**

- [`test_dependency_submission_is_a_separate_trusted_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_dependency_submission_is_a_separate_trusted_write_boundary`
- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`
- [`test_pages_validates_prs_and_confines_publish_permissions`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pages_validates_prs_and_confines_publish_permissions`
- [`test_pr_body_is_the_only_pull_request_target_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pr_body_is_the_only_pull_request_target_write_boundary`

### `CIR-004` - PR-body automation treats head content as data

**Contract:** PR-body automation MUST be the sole `pull_request_target`
boundary, execute only trusted default-branch code, read a bounded head
changelog through the API as inert data, preserve manual body content, and
reject missing or oversized managed release content.

**Evidence:**

- [`test_pr_body_is_the_only_pull_request_target_write_boundary`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_pr_body_is_the_only_pull_request_target_write_boundary`
- [`test_skips_empty_unreleased_and_preserves_manual_body`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_skips_empty_unreleased_and_preserves_manual_body`
- [`test_rejects_missing_release_entries`](../../tests/unit/test_pr_body.py) - `tests/unit/test_pr_body.py::test_rejects_missing_release_entries`

### `CIR-005` - Release version has one human-maintained owner

**Contract:** Root `.version` MUST be canonical stable SemVer and the only
human-maintained version. Package metadata MUST read it dynamically and the
agent manifest MUST match. A release-bearing change MUST increase the exact
base version and provide a matching non-empty dated changelog section.

**Evidence:**

- [`test_version_file_is_single_source`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_version_file_is_single_source`
- [`test_manifest_matches_version_file`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_manifest_matches_version_file`
- [`test_pyproject_reads_version_dynamically`](../../tests/unit/test_version.py) - `tests/unit/test_version.py::VersionSourceTest::test_pyproject_reads_version_dynamically`
- [`test_version_job_compares_exact_base_and_head`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_version_job_compares_exact_base_and_head`

### `CIR-006` - Publication reuses gated artifacts and never rewrites conflict

**Contract:** A not-yet-published version on main MUST pass reusable CI before
one publish job creates or resumes an exact draft Release, downloads the
smoke-tested platform artifacts, verifies the full inventory and checksums,
uploads exact assets, and publishes. Matching publication MUST be a read-only
no-op; conflicting tags, targets, metadata, or assets MUST fail without moving
or replacing history.

**Evidence:**

- [`test_release_reuses_ci_and_writes_only_in_publish_job`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_release_reuses_ci_and_writes_only_in_publish_job`
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
audit generated routes, links, anchors, canonical URLs, and assets, publish the
canonical domain root files, and grant deployment writes only to the deploy
job.

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
