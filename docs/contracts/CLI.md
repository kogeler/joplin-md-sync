<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# CLI And Output Contract

## Assertions

### `CLI-001` - JSON output is deterministic and isolated from diagnostics

**Contract:** Every command accepting `--json` MUST emit one valid,
key-sorted, ANSI-free JSON document on stdout. Logs and diagnostics MUST remain
on stderr. The envelope MUST contain `schema_version`, `command`, `success`,
`exit_code`, `code`, `tool_version`, and `workspace`.

**Evidence:**

- [`test_json_envelope_fields`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_json_envelope_fields`
- [`test_json_is_deterministic`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_json_is_deterministic`
- [`test_json_has_no_ansi_codes`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_json_has_no_ansi_codes`

### `CLI-002` - Exit codes and capabilities remain stable

**Contract:** Exit codes MUST retain these meanings: `0` success, `1`
differences or pending work, `2` unresolved conflicts, `3` invalid workspace
or managed file, `4` API or authentication failure, `5` concurrent change or
workspace lock, `6` partial operation or required recovery, `7` unsafe
operation blocked, `8` newer stable release available, and `9` internal
failure. `capabilities --json` MUST advertise the exact public command,
feature, schema, and exit-code surface.

**Evidence:**

- [`test_capabilities_report_exact_public_surface`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_capabilities_report_exact_public_surface`
- [`test_cli_reference_covers_every_public_option`](../../tests/unit/test_documentation_contracts.py) - `tests/unit/test_documentation_contracts.py::test_cli_reference_covers_every_public_option`
- [`test_internal_failure_is_stable`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_internal_failure_is_stable`
- [`test_diff_exit_codes`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_diff_exit_codes`
- [`test_divergent_edit_creates_conflict_without_overwrites`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_divergent_edit_creates_conflict_without_overwrites`
- [`test_ambiguous_write_not_applied_reports_failure`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RaceProtectionTest::test_ambiguous_write_not_applied_reports_failure`

### `CLI-003` - Connection and token precedence is deterministic

**Contract:** Joplin connection resolution MUST apply CLI options before
environment variables, workspace configuration, the loopback default, and
loopback discovery. Token-file input MUST override `JOPLIN_TOKEN`; raw token
values MUST NOT be accepted as CLI arguments except that the local-only
`mcp stdio` transport MUST require `--token`. Its Joplin `--port` MUST default
to `41184` without discovery. Zero or multiple discovered Clipper services
MUST fail unambiguously.

**Evidence:**

- [`test_cli_beats_everything`](../../tests/unit/test_config.py) - `tests/unit/test_config.py::ResolveBaseUrlTest::test_cli_beats_everything`
- [`test_env_base_url_beats_env_port_and_workspace`](../../tests/unit/test_config.py) - `tests/unit/test_config.py::ResolveBaseUrlTest::test_env_base_url_beats_env_port_and_workspace`
- [`test_token_file_beats_env`](../../tests/unit/test_config.py) - `tests/unit/test_config.py::ResolveTokenTest::test_token_file_beats_env`
- [`test_token_is_required_and_joplin_port_defaults_to_41184`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_token_is_required_and_joplin_port_defaults_to_41184`
- [`test_no_service_is_unambiguous_error`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::DiscoveryTest::test_no_service_is_unambiguous_error`
- [`test_multiple_services_is_unambiguous_error`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::DiscoveryTest::test_multiple_services_is_unambiguous_error`

### `CLI-004` - Inspection commands do not mutate state

**Contract:** `status` MUST operate from local and base state only. `diff`
MUST NOT mutate local files, workspace state, or Joplin in any output mode;
`--offline` MUST mark remote state unknown rather than implying equality.
`--exit-code` alone MAY convert detected differences to exit `1`.

**Evidence:**

- [`test_diff_never_mutates`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_diff_never_mutates`
- [`test_diff_offline_marks_remote_unknown`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_diff_offline_marks_remote_unknown`
- [`test_diff_note_filter`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::JsonContractTest::test_diff_note_filter`

### `CLI-005` - Workspace initialization is explicit and guarded

**Contract:** Remote-first initialization MUST reject an existing unmanaged
Markdown collection. Local-first initialization MAY adopt it, but MUST require
a successful push dry-run before the first real push. Reinitializing an
existing workspace MUST fail rather than replace state.

**Evidence:**

- [`test_remote_first_refuses_existing_markdown`](../../tests/integration/test_init_pull.py) - `tests/integration/test_init_pull.py::InitTest::test_remote_first_refuses_existing_markdown`
- [`test_local_first_requires_dry_run_before_push`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::LocalFirstModeTest::test_local_first_requires_dry_run_before_push`
- [`test_double_init_rejected`](../../tests/integration/test_init_pull.py) - `tests/integration/test_init_pull.py::InitTest::test_double_init_rejected`

### `CLI-006` - Health and update checks are bounded and explicit

**Contract:** `doctor --offline` MUST inspect local health without network
access. Online authentication failures MUST be distinguished from local
workspace failures. `update-check` MUST use stable Releases by default, MUST
never self-update, and MUST distinguish current, outdated, offline-skipped,
and network-failure outcomes.

**Evidence:**

- [`test_doctor_offline_skips_network`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::DoctorTest::test_doctor_offline_skips_network`
- [`test_doctor_reports_auth_failure`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::DoctorTest::test_doctor_reports_auth_failure`
- [`test_prereleases_excluded_by_default_uses_latest_endpoint`](../../tests/unit/test_update_check.py) - `tests/unit/test_update_check.py::CheckForUpdateTest::test_prereleases_excluded_by_default_uses_latest_endpoint`
- [`test_outdated_exit_code`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::UpdateCheckCliTest::test_outdated_exit_code`
- [`test_unreachable_github_is_operational_error`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::UpdateCheckCliTest::test_unreachable_github_is_operational_error`

### `CLI-007` - Note and resource helpers preserve managed content

**Contract:** Note metadata helpers MUST rewrite atomically and validate the
managed header. Resource pull MUST download referenced resources only into
workspace internal storage and MUST NOT rewrite `:/resource-id` or `:/note-id`
links.

**Evidence:**

- [`test_write_file_atomic_replaces_through_same_directory_temp`](../../tests/unit/test_workspace.py) - `tests/unit/test_workspace.py::test_write_file_atomic_replaces_through_same_directory_temp`
- [`test_tag_add_and_remove`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_tag_add_and_remove`
- [`test_title_change_renames_file_and_updates_remote`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_title_change_renames_file_and_updates_remote`
- [`test_note_validate_command`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::ResourcesTest::test_note_validate_command`
- [`test_resource_links_survive_round_trip`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::ResourcesTest::test_resource_links_survive_round_trip`
- [`test_resources_pull_downloads_files`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::ResourcesTest::test_resources_pull_downloads_files`
- [`test_note_links_not_treated_as_missing_resources`](../../tests/integration/test_discovery_resources.py) - `tests/integration/test_discovery_resources.py::ResourcesTest::test_note_links_not_treated_as_missing_resources`
