<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Security Contract

## Assertions

### `SEC-001` - Joplin credentials never enter public output or workspace state

**Contract:** The Joplin token MUST be accepted only from `JOPLIN_TOKEN` or a
token file, except that the local process `mcp stdio` MAY also accept the
compatibility argument `--token TOKEN`, whose value is visible to other local
processes and IDE configuration readers. Every Joplin, MCP, and Actions token
file MUST be a bounded single-line regular file opened without following
symlinks. On POSIX it MUST be owned by the current user with no group or other
access. On Windows it MUST be owned by the current user, SYSTEM, or
Administrators, and no effective allow entry of its ACL MAY give another
account read, write, or permission-change rights. Rejections MUST NOT echo the
file content. Every accepted form MUST be redacted from stdout, stderr, log
files, and logged tracebacks and MUST NOT be stored in workspace files. Stdio
stdout MUST contain only protocol messages, and stdio mode MUST NOT open HTTP
interfaces or accept their bearer credentials. Authentication failure MUST not
echo remote response content containing the credential.

**Evidence:**

- [`test_token_never_in_output_on_auth_error`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::TokenSafetyTest::test_token_never_in_output_on_auth_error`
- [`test_token_never_in_log_file`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::TokenSafetyTest::test_token_never_in_log_file`
- [`test_token_not_stored_in_workspace`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::TokenSafetyTest::test_token_not_stored_in_workspace`
- [`test_configuration_errors_never_write_to_protocol_stdout`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_configuration_errors_never_write_to_protocol_stdout`
- [`test_lifecycle_tools_live_call_and_protocol_errors`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_lifecycle_tools_live_call_and_protocol_errors`
- [`test_stdio_has_no_http_listener_or_interface_bearer_options`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_stdio_has_no_http_listener_or_interface_bearer_options`
- [`test_file_and_environment_tokens_are_redacted`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_file_and_environment_tokens_are_redacted`
- [`test_token_file_must_be_private_owned_and_not_a_symlink`](../../tests/unit/test_stdio_token.py) - `tests/unit/test_stdio_token.py::test_token_file_must_be_private_owned_and_not_a_symlink`
- [`test_token_file_content_is_bounded_single_line_ascii`](../../tests/unit/test_stdio_token.py) - `tests/unit/test_stdio_token.py::test_token_file_content_is_bounded_single_line_ascii`
- [`test_insecure_token_file_is_rejected_for_workspace_commands`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::TokenSafetyTest::test_insecure_token_file_is_rejected_for_workspace_commands`
- [`test_internal_error_traceback_is_redacted_on_stderr_and_log_file`](../../tests/integration/test_mcp_stdio.py) - `tests/integration/test_mcp_stdio.py::McpStdioCliTest::test_internal_error_traceback_is_redacted_on_stderr_and_log_file`
- [`test_windows_acl_accepts_private_files_owned_by_trusted_principals`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_windows_acl_accepts_private_files_owned_by_trusted_principals`
- [`test_windows_acl_rejects_foreign_owner_and_other_account_access`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_windows_acl_rejects_foreign_owner_and_other_account_access`
- [`test_windows_token_file_rejects_access_for_other_accounts`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_windows_token_file_rejects_access_for_other_accounts`

### `SEC-002` - Remote network exposure always requires explicit authorization

**Contract:** Joplin API addresses outside loopback MUST require
`--allow-remote-api`. MCP listeners outside loopback MUST require both
`--allow-remote-mcp` and protected MCP bearer authentication. Browser-origin
requests MUST pass the configured Origin policy.

**Evidence:**

- [`test_non_loopback_refused_without_flag`](../../tests/unit/test_config.py) - `tests/unit/test_config.py::ResolveBaseUrlTest::test_non_loopback_refused_without_flag`
- [`test_non_loopback_bind_requires_flag_and_auth`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpCliSafetyTest::test_non_loopback_bind_requires_flag_and_auth`
- [`test_http_validation_and_origin_protection`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_http_validation_and_origin_protection`

### `SEC-003` - Bearer sources are strong, protected, and rotatable

**Contract:** MCP and Actions bearer sources MUST be token files protected as
defined by `SEC-001` on POSIX and Windows, not symlinks or directories, and
contain one bounded URL-safe Base64 value encoding at least 256 bits.
Files MUST be re-read per request and comparisons MUST fail closed for
non-ASCII, malformed, oversized, or duplicate authorization.

**Evidence:**

- [`test_token_format_requires_bounded_urlsafe_base64_and_256_bits`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_token_format_requires_bounded_urlsafe_base64_and_256_bits`
- [`test_mcp_source_rejects_insecure_permissions_and_symlinks`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_mcp_source_rejects_insecure_permissions_and_symlinks`
- [`test_windows_token_file_rejects_access_for_other_accounts`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_windows_token_file_rejects_access_for_other_accounts`
- [`test_bearer_authentication_and_rotation`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_bearer_authentication_and_rotation`
- [`test_malformed_and_duplicate_authorization_fail_closed`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_malformed_and_duplicate_authorization_fail_closed`

### `SEC-004` - Filesystem inputs cannot escape or redirect traversal

**Contract:** Remote titles and local relative paths MUST be sanitized and
checked against the workspace root. Workspace scanning and bearer-file loading
MUST reject symlinks. Local replacements MUST be atomic, and destructive sync
operations MUST retain backup or quarantine evidence.

**Evidence:**

- [`test_traversal_rejected`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SafeRelPathTest::test_traversal_rejected`
- [`test_symlink_not_followed`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::SecurityScanTest::test_symlink_not_followed`
- [`test_mcp_source_rejects_insecure_permissions_and_symlinks`](../../tests/unit/test_auth.py) - `tests/unit/test_auth.py::test_mcp_source_rejects_insecure_permissions_and_symlinks`
- [`test_remote_delete_reported_then_quarantined`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PullChangesTest::test_remote_delete_reported_then_quarantined`

### `SEC-005` - Untrusted note content is data, never executable input

**Contract:** Runtime modules MUST NOT invoke a shell, subprocess, dynamic
`eval`, or dynamic `exec`. Note bodies, titles, tags, tool arguments, and tool
results MUST be treated as data and MUST not become filesystem paths or command
arguments outside their validated domain adapters.

**Evidence:**

- [`test_runtime_has_no_code_execution_surface`](../../tests/unit/test_security_policy.py) - `tests/unit/test_security_policy.py::test_runtime_has_no_code_execution_surface`
- [`test_http_and_schema_validation_precede_side_effects`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_http_and_schema_validation_precede_side_effects`

### `SEC-006` - Public Actions logs and contracts disclose no note content

**Contract:** Actions audit logs MAY contain request IDs, timing, sizes, effect,
status, and result class but MUST NOT contain authorization, arguments, results,
note content, or backend credentials. Generated OpenAPI MUST be token-free and
MUST expose only explicitly approved registry operations.

**Evidence:**

- [`test_backend_auth_is_not_public_actions_auth_and_logs_are_redacted`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_backend_auth_is_not_public_actions_auth_and_logs_are_redacted`
- [`test_contract_is_deterministic_and_secret_free`](../../tests/unit/test_gpt_openapi.py) - `tests/unit/test_gpt_openapi.py::test_contract_is_deterministic_and_secret_free`
- [`test_disabled_tools_require_reason_and_may_use_unsupported_schema`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_disabled_tools_require_reason_and_may_use_unsupported_schema`

### `SEC-007` - Destructive boundaries match Joplin recovery capabilities

**Contract:** Sync and direct note/notebook operations MUST use Joplin trash and
support restore. Tag and resource deletion MAY be permanent because Joplin has
no trash API for them and MUST be marked destructive in registry metadata.
Actions and MCP MUST report the actual deletion class.

**Evidence:**

- [`test_notebook_crud_nesting_notes_trash_and_restore`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_notebook_crud_nesting_notes_trash_and_restore`
- [`test_tag_crud_listing_and_note_relations`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_tag_crud_listing_and_note_relations`
- [`test_resource_crud_content_and_note_relations`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_resource_crud_content_and_note_relations`
- [`test_local_delete_propagated_to_trash`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_local_delete_propagated_to_trash`

### `SEC-008` - GitHub OAuth permits one verified numeric account

**Contract:** OAuth configuration MUST require one positive ASCII decimal GitHub
user ID. Login hints, email, unverified JWT claims and API scopes alone MUST NOT
authorize an account. Issuance, refresh and protected requests MUST verify the
current account via GitHub `/user` before downstream tokens are issued or a
tool runs. An upstream grant that GitHub already rotated MUST be retained when
that verification is temporarily unavailable, so the same downstream refresh
can be retried. Only within-request positive caching is permitted. Downstream
redirect URIs MUST exactly match the hosted Claude callback
`https://claude.ai/api/mcp/auth_callback`; DCR, PKCE, consent/state and one-use
code/refresh binding MUST remain enforced. Unverified registrations MUST stay
pending with a bounded count and lifetime until an owner login establishes
them; registration, token and browser-flow requests MUST have separate rate
budgets. Client credentials MUST remain independent across OAuth, legacy MCP,
Actions and Joplin, comparing the signing key as key material in every accepted
encoding. Codes, credentials and note bodies MUST NOT enter application/SDK
logs. GitHub expiring access and refresh token formats MUST be validated before
issuing client credentials.

**Evidence:**

- [`test_identity_uses_verified_id_and_normalized_api_scopes`](../../tests/oauth/unit/test_identity.py) - `tests/oauth/unit/test_identity.py::test_identity_uses_verified_id_and_normalized_api_scopes`
- [`test_foreign_github_account_cannot_issue_access_or_mutate`](../../tests/oauth/e2e/test_oauth_flow.py) - `tests/oauth/e2e/test_oauth_flow.py::test_foreign_github_account_cannot_issue_access_or_mutate`
- [`test_invalid_pkce_state_code_replay_and_redirect`](../../tests/oauth/e2e/test_oauth_flow.py) - `tests/oauth/e2e/test_oauth_flow.py::test_invalid_pkce_state_code_replay_and_redirect`
- [`test_concurrent_code_and_refresh_are_single_use`](../../tests/oauth/integration/test_provider_flow.py) - `tests/oauth/integration/test_provider_flow.py::test_concurrent_code_and_refresh_are_single_use`
- [`test_signed_wrong_audience_issuer_type_and_expired_tokens_are_rejected`](../../tests/oauth/integration/test_provider_flow.py) - `tests/oauth/integration/test_provider_flow.py::test_signed_wrong_audience_issuer_type_and_expired_tokens_are_rejected`
- [`test_explicit_refresh_survives_identity_outage_after_rotation`](../../tests/oauth/e2e/test_oauth_recovery.py) - `tests/oauth/e2e/test_oauth_recovery.py::test_explicit_refresh_survives_identity_outage_after_rotation`
- [`test_transparent_refresh_survives_identity_outage_without_running_tools`](../../tests/oauth/e2e/test_oauth_recovery.py) - `tests/oauth/e2e/test_oauth_recovery.py::test_transparent_refresh_survives_identity_outage_without_running_tools`
- [`test_owner_login_establishes_client_and_foreign_or_abandoned_stay_pending`](../../tests/oauth/integration/test_provider_flow.py) - `tests/oauth/integration/test_provider_flow.py::test_owner_login_establishes_client_and_foreign_or_abandoned_stay_pending`
- [`test_pending_registrations_expire_without_reset`](../../tests/oauth/integration/test_provider_flow.py) - `tests/oauth/integration/test_provider_flow.py::test_pending_registrations_expire_without_reset`
- [`test_dcr_registration_capacity_preserves_existing_owner_grant`](../../tests/oauth/e2e/test_oauth_lifecycle.py) - `tests/oauth/e2e/test_oauth_lifecycle.py::test_dcr_registration_capacity_preserves_existing_owner_grant`
- [`test_signing_key_reuse_is_rejected_in_any_accepted_encoding`](../../tests/oauth/unit/test_config.py) - `tests/oauth/unit/test_config.py::test_signing_key_reuse_is_rejected_in_any_accepted_encoding`

### `SEC-009` - OAuth state is private, encrypted and exclusively managed

**Contract:** Persistent OAuth credentials MUST be encrypted in a private,
dedicated directory outside sync state with an exclusive process lock and
validated schema. Incompatible/corrupt state or a wrong signing key MUST fail
closed without resetting existing grants. Reset MUST require explicit local
confirmation and the same exclusive lock. Restart MUST retain valid grants;
changing the allowed account or resetting state MUST deny old access. Quota or
storage failures MUST NOT evict existing valid grants to admit new ones.

**Evidence:**

- [`test_encrypted_state_restart_exclusive_lock_and_reset`](../../tests/oauth/unit/test_config.py) - `tests/oauth/unit/test_config.py::test_encrypted_state_restart_exclusive_lock_and_reset`
- [`test_corruption_and_wrong_key_preserve_state`](../../tests/oauth/unit/test_config.py) - `tests/oauth/unit/test_config.py::test_corruption_and_wrong_key_preserve_state`
- [`test_state_quota_invalid_ttl_and_disk_failure_preserve_existing_grants`](../../tests/oauth/unit/test_config.py) - `tests/oauth/unit/test_config.py::test_state_quota_invalid_ttl_and_disk_failure_preserve_existing_grants`
- [`test_changed_owner_and_explicit_local_reset_deny_old_grants`](../../tests/oauth/e2e/test_oauth_flow.py) - `tests/oauth/e2e/test_oauth_flow.py::test_changed_owner_and_explicit_local_reset_deny_old_grants`
