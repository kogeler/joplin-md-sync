<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Headless Service Contract

## Assertions

### `SVC-001` - Installer configuration is complete and deterministic

**Contract:** The headless installer MUST expose and document every supported
CLI option and environment override. Setup MUST work without CLI options by
asking only for missing values. CLI values MUST override environment values,
then existing configuration, then interactive answers/defaults. Optional
boolean questions MUST use `[y/N]` with Enter meaning no. Supplied settings
MUST replace their corresponding prompts. Current stable releases MUST be the
default for a new installation; saved version choices MUST survive updates.
Unsupported intervals/port collisions MUST fail before installation, and
non-interactive mode MUST never prompt.

**Evidence:**

- [`test_service_installer_reference_covers_every_public_option`](../../tests/unit/test_service_installer_docs.py) - `tests/unit/test_service_installer_docs.py::test_service_installer_reference_covers_every_public_option`
- [`test_service_installer_reference_covers_every_environment_override`](../../tests/unit/test_service_installer_docs.py) - `tests/unit/test_service_installer_docs.py::test_service_installer_reference_covers_every_environment_override`
- [`test_cli_overrides_environment`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ParserTests::test_cli_overrides_environment`
- [`test_dry_run_uses_the_same_disabled_startup_defaults_as_installation`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_dry_run_uses_the_same_disabled_startup_defaults_as_installation`
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
Secret prompts MUST refuse getpass's echo fallback. A non-regular credential
path, including a FIFO with no writer, MUST fail without blocking.

**Evidence:**

- [`test_secret_file_rejects_unsafe_mode_and_symlink`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_secret_file_rejects_unsafe_mode_and_symlink`
- [`test_hidden_input_refuses_getpass_echo_fallback`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_hidden_input_refuses_getpass_echo_fallback`
- [`test_secret_fifo_is_rejected_without_blocking`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_secret_fifo_is_rejected_without_blocking`
- [`test_child_environment_removes_every_service_secret`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_child_environment_removes_every_service_secret`
- [`test_service_tokens_are_generated_and_preserved`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_service_tokens_are_generated_and_preserved`
- [`test_duplicate_existing_service_tokens_are_rejected`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_duplicate_existing_service_tokens_are_rejected`
- [`test_settings_use_stdin_not_process_arguments_and_preserve_json_types`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_settings_use_stdin_not_process_arguments_and_preserve_json_types`
- [`test_s3_access_key_file_is_protected_and_never_a_child_argument`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_s3_access_key_file_is_protected_and_never_a_child_argument`

### `SVC-004` - Sync targets map to explicit Joplin settings

**Contract:** Every supported static sync target MUST map to its complete
Joplin setting set. Browser-authorized targets MUST persist and verify browser
authentication before service start; password-authorized and S3 targets MUST
require their complete non-interactive credentials. Existing conflicting
profile configuration MUST require an explicit CLI selection, interactive
selection, or `--reconfigure`. Unchanged target/account/endpoint credentials
MUST be reused through the supported Joplin CLI without another secret prompt;
a changed endpoint/account MUST NOT silently receive the previous secret.
Account names and S3 bucket/region values MUST compare exactly; only URL
normalization actually applied to the driver settings may be ignored.
The `none` backend MUST disable synchronization.
S3 MUST ask for both missing access and secret keys with hidden interactive
prompts by default; credential files MUST remain optional and replace only
their corresponding prompts. Non-interactive mode MUST fail without prompting
when either credential is absent.
Initial sync MUST prove a completed report without synchronization errors,
not merely a zero process exit code. Custom S3 endpoint paths, regions,
bucket names, credentials, and path-style policy MUST reach the real driver.

**Evidence:**

- [`test_each_static_target_maps_to_joplin_settings`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_each_static_target_maps_to_joplin_settings`
- [`test_changed_account_name_never_receives_the_previous_password`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_changed_account_name_never_receives_the_previous_password`
- [`test_reconfigure_existing_profile_without_units_clears_unused_credentials`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_reconfigure_existing_profile_without_units_clears_unused_credentials`
- [`test_s3_maps_every_driver_setting`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_s3_maps_every_driver_setting`
- [`test_browser_sync_requires_persisted_auth_before_verification`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_browser_sync_requires_persisted_auth_before_verification`
- [`test_noninteractive_conflict_requires_force`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_noninteractive_conflict_requires_force`
- [`test_s3_defaults_prompt_for_both_keys_without_echo`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_s3_defaults_prompt_for_both_keys_without_echo`
- [`test_s3_optional_files_replace_only_their_corresponding_prompts`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_s3_optional_files_replace_only_their_corresponding_prompts`
- [`test_s3_noninteractive_missing_keys_fails_before_reading_or_prompting`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::SecretTests::test_s3_noninteractive_missing_keys_fails_before_reading_or_prompting`
- [`test_custom_s3_example_defaults_to_interactive_credentials`](../../tests/unit/test_service_installer_docs.py) - `tests/unit/test_service_installer_docs.py::test_custom_s3_example_defaults_to_interactive_credentials`
- [`test_sync_rejects_zero_exit_with_error_or_incomplete_report_and_restores_locale`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ConfigurationTests::test_sync_rejects_zero_exit_with_error_or_incomplete_report_and_restores_locale`
- [`test_install_configure_sync_and_download_with_custom_endpoint`](../../scripts/joplin_terminal_service/acceptance/test_s3.py) - `scripts/joplin_terminal_service/acceptance/test_s3.py::RealJoplinS3Tests::test_install_configure_sync_and_download_with_custom_endpoint`

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

**Contract:** A default rerun MUST detect the installed services and update
them; `--upgrade` MUST remain an explicit compatibility option. Upgrade MUST
update Joplin Terminal and `joplin-md-sync`
independently without re-requesting stored sync secrets, preserve protected
service tokens, restart both units in dependency order, and require bounded
Joplin, MCP, and Actions smoke checks. Failed health checks MUST stop the
restart loop of both units and retain recoverable prior executable/service
files without copying or automatically downgrading a Joplin profile. A changed
`joplin-md-sync` executable MUST be downloaded, verified and smoke-tested
before healthy services are stopped. Upgrade MUST preserve installed paths, ports, interval, listener policy and filesystem
sandbox exceptions unless explicitly overridden, and refresh both units and
supervisor helpers. Unreadable or inconsistent installed units MUST fail
before service changes. Saved startup/version choices MUST survive reruns.
An incomplete pair with only one installed unit MUST be rejected before any
setup prompt or overwrite. Cleaning an incomplete or unreadable/inconsistent
pair MUST require its explicit profile and prefix paths, without prompting
or guessing default paths.
`--reconfigure` MUST skip existing settings when asking for this run's choices,
apply explicit/interactive enablement, and clear inactive managed configuration
through supported interfaces without deleting existing notes.

**Evidence:**

- [`test_upgrade_updates_both_without_resolving_sync_secrets`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::DryRunTests::test_upgrade_updates_both_without_resolving_sync_secrets`
- [`test_partial_service_pair_is_rejected_before_prompts_or_changes`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_partial_service_pair_is_rejected_before_prompts_or_changes`
- [`test_inconsistent_service_credential_path_is_rejected_before_service_changes`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_inconsistent_service_credential_path_is_rejected_before_service_changes`
- [`test_upgrade_restarts_both_services_and_smokes_both_api_routes`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_upgrade_restarts_both_services_and_smokes_both_api_routes`
- [`test_failed_upgrade_healthcheck_stops_restart_loop`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_failed_upgrade_healthcheck_stops_restart_loop`
- [`test_upgrade_preserves_custom_units_deploys_helpers_and_keeps_old_files`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_upgrade_preserves_custom_units_deploys_helpers_and_keeps_old_files`
- [`test_upgrade_explicit_options_override_units_and_invalid_units_fail_closed`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_upgrade_explicit_options_override_units_and_invalid_units_fail_closed`
- [`test_failed_adapter_download_keeps_healthy_services_running`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::ServiceLifecycleTests::test_failed_adapter_download_keeps_healthy_services_running`

### `SVC-007` - Purge is explicit, scoped, and idempotent

**Contract:** `--clean` and its `--purge` alias MUST run without questions or
a confirmation flag, bypass setup/credential collection, obtain the profile
lock before deletion,
remove only managed files and units, preserve unrelated user data, and be safe
to repeat. Dry-run MUST delete nothing and MUST not require a working systemd
bus.

**Evidence:**

- [`test_full_purge_removes_only_managed_local_paths_and_is_idempotent`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_full_purge_removes_only_managed_local_paths_and_is_idempotent`
- [`test_profile_lock_blocks_purge_before_data_removal`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_profile_lock_blocks_purge_before_data_removal`
- [`test_noninteractive_clean_needs_no_yes_and_never_prompts`](../../scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py) - `scripts/joplin_terminal_service/tests/test_install_joplin_terminal.py::PurgeTests::test_noninteractive_clean_needs_no_yes_and_never_prompts`
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

### `SVC-009` - OAuth extends the existing service and preserves operator data

**Contract:** The existing installer MUST enable OAuth on the same adapter unit
and port through `--github-oauth`. Upgrade MUST validate the candidate's
OAuth configuration before stopping a healthy service or replacing its binary.
OAuth state writes MUST receive a scoped sandbox exception; existing system
hardening MUST remain. Ordinary OAuth disablement MUST preserve config and
grants. During explicit `--reconfigure`, disabled OAuth MUST lose its saved
configuration, owned secret files and registered managed grants; external
operator-owned files MUST be retained. Enabling OAuth with missing settings
MUST ask for them, hide the Client Secret, and generate an owner-only signing
key when no key is supplied. Purge MUST remove only installer-registered
managed OAuth state and MUST preserve operator-owned custom paths and external
secrets.

**Evidence:**

- [`test_oauth_preflight_is_readonly_and_unit_has_scoped_write_path`](../../tests/oauth/integration/test_installer_oauth.py) - `tests/oauth/integration/test_installer_oauth.py::test_oauth_preflight_is_readonly_and_unit_has_scoped_write_path`
- [`test_unregistered_custom_oauth_state_is_preserved`](../../tests/oauth/integration/test_installer_oauth.py) - `tests/oauth/integration/test_installer_oauth.py::test_unregistered_custom_oauth_state_is_preserved`
- [`test_failed_oauth_candidate_check_keeps_healthy_services_and_old_binary`](../../tests/oauth/integration/test_installer_oauth.py) - `tests/oauth/integration/test_installer_oauth.py::test_failed_oauth_candidate_check_keeps_healthy_services_and_old_binary`

### `SVC-010` - The remote entry point imports a verified temporary package

**Contract:** The installer entry point MUST use normal imports from a local
sibling package. Downloaded/stdin runs MUST download the complete package and
service assets into a private `/tmp` directory, verify the manifest digest
pinned by the entry point and every file digest before importing application
code, select all companion files from the requested branch/tag/commit
(`--installer-ref` or its environment equivalent, default `main`) without a
fallback to `main`, bound file/bundle sizes and reject unsafe relative paths.
Temporary files MUST be removed after normal exit or an exception. Deployed services
MUST use persistent copied files, independent of the temporary package.

**Evidence:**

- [`test_zero_argument_install_and_update_preserve_sync_without_any_prompt`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_zero_argument_install_and_update_preserve_sync_without_any_prompt`
- [`test_remote_bundle_tampering_fails_before_setup_and_removes_temporary_files`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_remote_bundle_tampering_fails_before_setup_and_removes_temporary_files`
- [`test_partial_oauth_flags_prompt_only_missing_values_and_reconfigure_removes_owned_secrets`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_partial_oauth_flags_prompt_only_missing_values_and_reconfigure_removes_owned_secrets`
- [`test_clean_never_prompts_and_removes_only_the_managed_installation`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_clean_never_prompts_and_removes_only_the_managed_installation`
- [`test_remote_branch_tag_and_commit_selection_never_falls_back_to_main`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_remote_branch_tag_and_commit_selection_never_falls_back_to_main`
- [`test_remote_ref_environment_and_main_default_select_the_entire_bundle`](../../scripts/joplin_terminal_service/tests/test_wizard_process.py) - `scripts/joplin_terminal_service/tests/test_wizard_process.py::WizardProcessTests::test_remote_ref_environment_and_main_default_select_the_entire_bundle`
