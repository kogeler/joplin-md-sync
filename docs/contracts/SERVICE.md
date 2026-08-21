<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Headless Service Contract

## Assertions

### `SVC-001` - Installer configuration is complete and deterministic

**Contract:** The headless installer MUST expose and document every supported
CLI option and environment override. CLI values MUST override environment
values, current stable releases MUST be the default, unsupported intervals and
port collisions MUST fail before installation, and non-interactive mode MUST
never prompt.

**Evidence:**

- [`test_service_installer_reference_covers_every_public_option`](../../tests/unit/test_service_installer_docs.py) - `tests/unit/test_service_installer_docs.py::test_service_installer_reference_covers_every_public_option`
- [`test_service_installer_reference_covers_every_environment_override`](../../tests/unit/test_service_installer_docs.py) - `tests/unit/test_service_installer_docs.py::test_service_installer_reference_covers_every_environment_override`
- [`test_cli_overrides_environment`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ParserTests::test_cli_overrides_environment`
- [`test_non_interactive_never_prompts`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_non_interactive_never_prompts`

### `SVC-002` - Installation is isolated and systemd units contain no secrets

**Contract:** Joplin Terminal, its profile, npm prefix, helper scripts,
credentials, and the `joplin-md-sync` binary MUST use the managed per-user
layout. Generated units MUST use absolute paths, contain no secret values, pass
systemd verification where available, and retain reviewed sandbox exceptions
for snap and filesystem sync targets.

**Evidence:**

- [`test_isolated_layout`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::UnitAndPathTests::test_isolated_layout`
- [`test_rendered_unit_has_no_secrets_and_absolute_paths`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::UnitAndPathTests::test_rendered_unit_has_no_secrets_and_absolute_paths`
- [`test_filesystem_target_is_writable_inside_service_sandbox`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::UnitAndPathTests::test_filesystem_target_is_writable_inside_service_sandbox`
- [`test_rendered_unit_passes_systemd_analyze`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::UnitAndPathTests::test_rendered_unit_passes_systemd_analyze`

### `SVC-003` - Service credentials are protected, distinct, and never leaked

**Contract:** Joplin, E2EE, MCP, Actions, and sync credentials MUST be read from
protected regular files or bounded interactive prompts and MUST be removed from
child environments, commands, logs, units, and reports. Generated MCP and
Actions bearer tokens MUST be preserved across reruns and MUST be distinct
from each other and from the Joplin token.

**Evidence:**

- [`test_secret_file_rejects_unsafe_mode_and_symlink`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_secret_file_rejects_unsafe_mode_and_symlink`
- [`test_child_environment_removes_every_service_secret`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_child_environment_removes_every_service_secret`
- [`test_service_tokens_are_generated_and_preserved`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_service_tokens_are_generated_and_preserved`
- [`test_duplicate_existing_service_tokens_are_rejected`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_duplicate_existing_service_tokens_are_rejected`

### `SVC-004` - Sync targets map to explicit Joplin settings

**Contract:** Every supported static sync target MUST map to its complete
Joplin setting set. Browser-authorized targets MUST persist and verify browser
authentication before service start; password-authorized and S3 targets MUST
require their complete non-interactive credentials. Existing conflicting
profile configuration MUST require explicit interactive or force approval.

**Evidence:**

- [`test_each_static_target_maps_to_joplin_settings`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_each_static_target_maps_to_joplin_settings`
- [`test_s3_maps_every_driver_setting`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_s3_maps_every_driver_setting`
- [`test_browser_sync_requires_persisted_auth_before_verification`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_browser_sync_requires_persisted_auth_before_verification`
- [`test_noninteractive_conflict_requires_force`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_noninteractive_conflict_requires_force`

### `SVC-005` - Release downloads are exact and verified

**Contract:** The installer MUST resolve stable release metadata, accept only
the supported platform asset name at the expected GitHub Release URL, verify
its exact SHA-256 entry before replacement, and refuse unknown existing
binaries or malformed release tags. Installation MUST be idempotent.

**Evidence:**

- [`test_architecture_asset_mapping`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::McpReleaseTests::test_architecture_asset_mapping`
- [`test_download_verifies_checksum_and_is_idempotent`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::McpReleaseTests::test_download_verifies_checksum_and_is_idempotent`
- [`test_checksum_mismatch_does_not_install_binary`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::McpReleaseTests::test_checksum_mismatch_does_not_install_binary`
- [`test_unknown_existing_binary_is_not_overwritten`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::McpReleaseTests::test_unknown_existing_binary_is_not_overwritten`

### `SVC-006` - Upgrades preserve configuration and prove both APIs

**Contract:** Upgrade MUST update Joplin Terminal and `joplin-md-sync`
independently without re-requesting stored sync secrets, preserve protected
service tokens, restart both units in dependency order, and require bounded
Joplin, MCP, and Actions smoke checks. Failed health checks MUST stop the
restart loop and retain recoverable prior files.

**Evidence:**

- [`test_upgrade_updates_both_without_resolving_sync_secrets`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::DryRunTests::test_upgrade_updates_both_without_resolving_sync_secrets`
- [`test_upgrade_restarts_both_services_and_smokes_both_api_routes`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_upgrade_restarts_both_services_and_smokes_both_api_routes`
- [`test_failed_upgrade_healthcheck_stops_restart_loop`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_failed_upgrade_healthcheck_stops_restart_loop`

### `SVC-007` - Purge is explicit, scoped, and idempotent

**Contract:** Purge MUST require exact interactive confirmation or explicit
`--yes` in non-interactive mode, obtain the profile lock before deletion,
remove only managed files and units, preserve unrelated user data, and be safe
to repeat. Dry-run MUST delete nothing and MUST not require a working systemd
bus.

**Evidence:**

- [`test_full_purge_removes_only_managed_local_paths_and_is_idempotent`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_full_purge_removes_only_managed_local_paths_and_is_idempotent`
- [`test_profile_lock_blocks_purge_before_data_removal`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_profile_lock_blocks_purge_before_data_removal`
- [`test_noninteractive_purge_requires_yes`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_noninteractive_purge_requires_yes`
- [`test_purge_dry_run_does_not_require_systemctl_or_delete_data`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_purge_dry_run_does_not_require_systemctl_or_delete_data`

### `SVC-008` - The supervisor fails closed and terminates cleanly

**Contract:** The Joplin Terminal supervisor MUST unlock E2EE without echoing
the secret, bound sensitive child output, verify API readiness, propagate child
failure through nonzero exit for systemd restart, terminate hung children, and
reject port collisions, read-only profiles, and failed runtime self-checks
before reporting readiness.

**Evidence:**

- [`test_success_and_secret_not_echoed`](../../scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py::E2eePtyTests::test_success_and_secret_not_echoed`
- [`test_large_sensitive_output_is_bounded_and_heartbeat_is_safe`](../../scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py::E2eePtyTests::test_large_sensitive_output_is_bounded_and_heartbeat_is_safe`
- [`test_health_loss_restarts_via_failure_exit`](../../scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py::SupervisorTests::test_health_loss_restarts_via_failure_exit`
- [`test_hung_child_is_killed_without_orphan`](../../scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_run_joplin_terminal.py::SupervisorTests::test_hung_child_is_killed_without_orphan`
