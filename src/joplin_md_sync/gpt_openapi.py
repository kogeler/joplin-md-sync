"""Deterministic OpenAPI 3.1 generation for ChatGPT Custom GPT Actions."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from joplin_md_sync import __version__
from joplin_md_sync.api import JoplinClient
from joplin_md_sync.mcp_service import JoplinMcpService
from joplin_md_sync.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    action_route,
    build_tool_registry,
    operation_id,
    tool_effect,
)
from joplin_md_sync.tool_schema import mutable_json
from joplin_md_sync.workspace import write_file_atomic

OPENAPI_VERSION = "3.1.0"
CANONICAL_SERVER_URL = "https://joplin.example.invalid"
ACTION_PATH_PREFIX = "/api/gpt/v1/tools"
MAX_OPERATION_DESCRIPTION_CHARS = 300
MAX_PARAMETER_DESCRIPTION_CHARS = 700

JsonObject = dict[str, Any]


def registry_for_export() -> ToolRegistry:
    """Build definitions without resolving or contacting a Joplin endpoint."""

    def unavailable_client() -> JoplinClient:
        raise RuntimeError("OpenAPI export does not execute tool handlers")

    return build_tool_registry(
        JoplinMcpService(unavailable_client, availability_timeout=0)
    )


def validate_server_url(server_url: str, *, allow_http_for_tests: bool = False) -> str:
    normalized = server_url.rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    allowed_schemes = {"https"} | ({"http"} if allow_http_for_tests else set())
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ValueError("Actions server URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Actions server URL must not contain credentials, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Actions server URL contains an invalid port") from exc
    if not allow_http_for_tests and port not in (None, 443):
        raise ValueError("Actions server URL must use public HTTPS port 443")
    if parsed.path not in ("", "/"):
        raise ValueError("Actions server URL must not contain a path")
    return normalized


def _public_tool_data(tool: ToolDefinition) -> JsonObject:
    return {
        "action_exposure": tool.action_exposure,
        "action_exposure_reason": tool.action_exposure_reason,
        "annotations": mutable_json(tool.annotations),
        "description": tool.description,
        "input_schema": mutable_json(tool.input_schema),
        "name": tool.name,
        "output_schema": mutable_json(tool.output_schema),
        "title": tool.title,
    }


def registry_hash(registry: ToolRegistry) -> str:
    canonical = json.dumps(
        [_public_tool_data(tool) for tool in registry.definitions],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _check_descriptions(tool: ToolDefinition) -> None:
    for field_name, text in (("title", tool.title), ("description", tool.description)):
        if len(text) > MAX_OPERATION_DESCRIPTION_CHARS:
            raise ValueError(
                f"tool {tool.name} {field_name} exceeds "
                f"{MAX_OPERATION_DESCRIPTION_CHARS} characters"
            )

    def walk(schema: Mapping[str, object], path: str) -> None:
        description = schema.get("description")
        if isinstance(description, str) and len(description) > MAX_PARAMETER_DESCRIPTION_CHARS:
            raise ValueError(
                f"tool {tool.name} parameter {path} description exceeds "
                f"{MAX_PARAMETER_DESCRIPTION_CHARS} characters"
            )
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for name, child in properties.items():
                if isinstance(name, str) and isinstance(child, Mapping):
                    walk(child, f"{path}.{name}")
        items = schema.get("items")
        if isinstance(items, Mapping):
            walk(items, f"{path}[]")

    walk(tool.input_schema, "$")


def _success_schema() -> JsonObject:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "success": {"type": "boolean", "const": True},
            "result": {"type": "object", "additionalProperties": True},
            "request_id": {"type": "string"},
        },
        "required": ["success", "result", "request_id"],
    }


def _error_schema() -> JsonObject:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "success": {"type": "boolean", "const": False},
            "error": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "retryable": {"type": "boolean"},
                },
                "required": ["code", "message", "retryable"],
            },
            "request_id": {"type": "string"},
        },
        "required": ["success", "error", "request_id"],
    }


def _response(description: str, schema_ref: str) -> JsonObject:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": schema_ref}}
        },
    }


def generate_openapi(registry: ToolRegistry, server_url: str) -> JsonObject:
    server_url = validate_server_url(server_url)
    paths: JsonObject = {}
    error_responses = {
        "400": "Malformed JSON or HTTP request",
        "401": "Authentication required",
        "404": "Unknown Action route",
        "405": "Method not allowed",
        "413": "Request body too large",
        "415": "Unsupported media type",
        "422": "Tool arguments are invalid",
        "429": "Rate limit exceeded",
        "500": "Internal failure",
        "502": "Upstream or ambiguous result failure",
        "503": "Service unavailable",
        "504": "Confirmed upstream timeout",
    }
    for tool in registry.exposed:
        _check_descriptions(tool)
        effect = tool_effect(tool)
        responses: JsonObject = {
            "200": {"$ref": "#/components/responses/ActionSuccess"}
        }
        responses.update(
            {
                status: {"$ref": f"#/components/responses/Action{status}"}
                for status in error_responses
            }
        )
        paths[f"{ACTION_PATH_PREFIX}/{action_route(tool)}"] = {
            "post": {
                "operationId": operation_id(tool),
                "summary": tool.title,
                "description": tool.description,
                "security": [{"GPTActionBearer": []}],
                "x-openai-isConsequential": effect != "read",
                "x-joplin-md-sync-effect": effect,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": mutable_json(tool.input_schema)
                        }
                    },
                },
                "responses": responses,
            }
        }
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "joplin-md-sync GPT Actions",
            "description": "Private authenticated access to the shared Joplin tool registry.",
            "version": __version__,
        },
        "servers": [{"url": server_url}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "GPTActionBearer": {"type": "http", "scheme": "bearer"}
            },
            "schemas": {
                "ActionSuccess": _success_schema(),
                "ActionError": _error_schema(),
            },
            "responses": {
                "ActionSuccess": _response(
                    "Tool completed successfully",
                    "#/components/schemas/ActionSuccess",
                ),
                **{
                    f"Action{status}": _response(
                        description, "#/components/schemas/ActionError"
                    )
                    for status, description in error_responses.items()
                },
            },
        },
        "x-joplin-md-sync-tool-registry-hash": registry_hash(registry),
    }


def render_openapi(registry: ToolRegistry, server_url: str) -> str:
    return json.dumps(
        generate_openapi(registry, server_url),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def export_openapi(output: Path, server_url: str) -> str:
    rendered = render_openapi(registry_for_export(), server_url)
    write_file_atomic(output, rendered)
    return registry_hash(registry_for_export())
