<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Agent Interface Contract

## Assertions

### `AIF-001` - One immutable registry owns every agent operation

**Contract:** MCP and GPT Actions MUST derive tool names, schemas, effects,
handlers, and exposure from one ordered immutable registry and invoke handlers
through one validating executor. HTTP adapters MUST NOT maintain a second
operation list or bypass schema validation and failure classification.

**Evidence:**

- [`test_registry_is_ordered_exact_and_deeply_immutable`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_registry_is_ordered_exact_and_deeply_immutable`
- [`test_effect_and_route_are_centralized`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_effect_and_route_are_centralized`
- [`test_executor_validates_and_preserves_domain_errors`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_executor_validates_and_preserves_domain_errors`
- [`test_generated_operations_match_exposed_registry`](../../tests/unit/test_gpt_openapi.py) - `tests/unit/test_gpt_openapi.py::test_generated_operations_match_exposed_registry`

### `AIF-002` - MCP follows the advertised Streamable HTTP lifecycle

**Contract:** `mcp serve` MUST expose MCP Streamable HTTP on loopback
`/mcp` by default, negotiate only its supported protocol versions, implement
initialize/initialized, tools/list, and tools/call, and return both text content
and `structuredContent`. The server MUST advertise itself through CLI
capabilities and MUST NOT require a Markdown workspace.

**Evidence:**

- [`test_lifecycle_and_transport_contract`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_lifecycle_and_transport_contract`
- [`test_capabilities_advertise_mcp`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpCliSafetyTest::test_capabilities_advertise_mcp`

### `AIF-003` - MCP exposes complete Joplin object workflows

**Contract:** MCP MUST support validated note, notebook, tag, and resource
read/write workflows, relationship traversal, search, trash/restore for notes
and notebooks, and permanent deletion only where Joplin has no trash API.
Binary content MUST enter and leave as bounded base64 data, never a server-side
path.

**Evidence:**

- [`test_note_crud_metadata_tags_and_search`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_note_crud_metadata_tags_and_search`
- [`test_notebook_crud_nesting_notes_trash_and_restore`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_notebook_crud_nesting_notes_trash_and_restore`
- [`test_tag_crud_listing_and_note_relations`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_tag_crud_listing_and_note_relations`
- [`test_resource_crud_content_and_note_relations`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_resource_crud_content_and_note_relations`
- [`test_create_note_from_html_and_with_binary_attachments`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_create_note_from_html_and_with_binary_attachments`

### `AIF-004` - Invalid operations fail before side effects

**Contract:** Tool definitions and instances MUST pass the supported JSON
Schema subset before handler execution. Unknown objects, invalid content,
unsupported combinations, and Joplin failures MUST return structured stable
domain errors rather than transport crashes or partial writes.

**Evidence:**

- [`test_schema_definition_and_instance_validation`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_schema_definition_and_instance_validation`
- [`test_entity_and_content_validation_errors_are_structured`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_entity_and_content_validation_errors_are_structured`
- [`test_http_and_schema_validation_precede_side_effects`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_http_and_schema_validation_precede_side_effects`

### `AIF-005` - Joplin outages do not terminate the bridge

**Contract:** Binding the agent listener MUST NOT require Joplin availability.
Each call MAY perform a bounded availability wait and retry reads. An outage
MUST return a retryable structured tool error and later calls MUST recover.
Writes MUST be sent once after preflight; ambiguous outcomes MUST be
non-retryable and exposed for operator inspection.

**Evidence:**

- [`test_joplin_outage_is_retryable_tool_error_and_server_recovers`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_joplin_outage_is_retryable_tool_error_and_server_recovers`
- [`test_executor_classifies_expected_failures`](../../tests/unit/test_tool_registry.py) - `tests/unit/test_tool_registry.py::test_executor_classifies_expected_failures`

### `AIF-006` - Actions authentication is independent and fail-closed

**Contract:** GPT Actions MUST remain disabled unless explicitly enabled with
a dedicated protected bearer file. Authentication MUST occur before route
lookup, files MUST be re-read per request for rotation, malformed or duplicate
authorization MUST fail closed, and Joplin, MCP, and Actions credentials MUST
remain independent.

**Evidence:**

- [`test_authentication_is_uniform_independent_and_rotatable`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_authentication_is_uniform_independent_and_rotatable`
- [`test_malformed_and_duplicate_authorization_fail_closed`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_malformed_and_duplicate_authorization_fail_closed`
- [`test_route_is_fail_closed_and_auth_hides_route_details`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_route_is_fail_closed_and_auth_hides_route_details`
- [`test_mcp_actions_and_joplin_credentials_are_independent`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_mcp_actions_and_joplin_credentials_are_independent`

### `AIF-007` - OpenAPI is generated, deterministic, and secret-free

**Contract:** The Actions OpenAPI 3.1 document MUST be generated from the
exposed registry for one strict production HTTPS origin. It MUST be
deterministic and contain no credential, note data, local path, upstream Joplin
URL, MCP route, or health route. Disabled operations MUST not be exported.

**Evidence:**

- [`test_contract_is_deterministic_and_secret_free`](../../tests/unit/test_gpt_openapi.py) - `tests/unit/test_gpt_openapi.py::test_contract_is_deterministic_and_secret_free`
- [`test_production_server_url_is_strict`](../../tests/unit/test_gpt_openapi.py) - `tests/unit/test_gpt_openapi.py::test_production_server_url_is_strict`
- [`test_cli_exports_contract_and_advertises_optional_transport`](../../tests/unit/test_gpt_openapi.py) - `tests/unit/test_gpt_openapi.py::test_cli_exports_contract_and_advertises_optional_transport`

### `AIF-008` - Public transports enforce bounded resource use

**Contract:** MCP and Actions MUST bound request size, decoded resource size,
JSON nesting, active handlers, and stalled connections. Actions MUST also
bound response size, concurrent execution, and authenticated request rate.
Oversized successful write results MAY be replaced by an explicit omitted
result but MUST NOT be reported as a failed write.

**Evidence:**

- [`test_pre_auth_connection_count_is_bounded`](../../tests/integration/test_mcp_server.py) - `tests/integration/test_mcp_server.py::McpHttpTest::test_pre_auth_connection_count_is_bounded`
- [`test_request_limit_and_result_limit_semantics`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_request_limit_and_result_limit_semantics`
- [`test_concurrency_capacity_and_authenticated_rate_limit`](../../tests/integration/test_gpt_actions.py) - `tests/integration/test_gpt_actions.py::GptActionsHttpTest::test_concurrency_capacity_and_authenticated_rate_limit`
- [`test_json_nesting_limit_is_independent_of_parser_recursion_behavior`](../../tests/unit/test_json_safety.py) - `tests/unit/test_json_safety.py::test_json_nesting_limit_is_independent_of_parser_recursion_behavior`
