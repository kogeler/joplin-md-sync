"""Shared tool registry, schema subset, and executor contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.api import AmbiguousWriteError
from joplin_md_sync.errors import ApiError, AuthError, JoplinSyncError
from joplin_md_sync.mcp_service import ToolServiceError
from joplin_md_sync.tool_executor import ToolExecutor
from joplin_md_sync.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    action_route,
    tool_effect,
)
from joplin_md_sync.tool_schema import (
    SchemaDefinitionError,
    SchemaValidationError,
    validate_instance,
    validate_schema_definition,
)


def definition(
    name: str = "read_tool",
    *,
    schema: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
    exposure: str = "auto",
    reason: str | None = None,
    handler: Any = None,
) -> ToolDefinition:
    def default_handler(arguments: Any) -> dict[str, Any]:
        return {"arguments": dict(arguments)}

    return ToolDefinition(
        name=name,
        title="Read tool",
        description="Read test data.",
        input_schema=schema
        or {"type": "object", "properties": {}, "additionalProperties": False},
        annotations=(
            annotations
            if annotations is not None
            else {"readOnlyHint": True, "destructiveHint": False}
        ),
        handler=handler or default_handler,
        action_exposure=cast(Any, exposure),
        action_exposure_reason=reason,
    )


def test_registry_is_ordered_exact_and_deeply_immutable() -> None:
    first = definition("first")
    second = definition("second")
    registry = ToolRegistry((first, second))
    assert [tool.name for tool in registry] == ["first", "second"]
    assert registry.get("first") is first
    assert registry.get("FIRST") is None
    with pytest.raises(TypeError):
        cast(Any, first.input_schema)["type"] = "string"


def test_registry_rejects_duplicate_missing_exposure_and_bad_effect() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry((definition("same"), definition("same")))
    with pytest.raises(ValueError, match="exposure"):
        ToolRegistry((definition(exposure=""),))
    with pytest.raises(ValueError, match="both read-only and destructive"):
        ToolRegistry(
            (
                definition(
                    annotations={"readOnlyHint": True, "destructiveHint": True}
                ),
            )
        )
    with pytest.raises(ValueError, match="unknown effect"):
        ToolRegistry((definition(annotations={}),))


def test_disabled_tools_require_reason_and_may_use_unsupported_schema() -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        ToolRegistry((definition(exposure="disabled"),))
    disabled = definition(
        exposure="disabled",
        reason="Not compatible.",
        schema={"type": "object", "oneOf": []},
    )
    registry = ToolRegistry((disabled, definition("visible")))
    assert registry.exposed == (registry.get("visible"),)


def test_schema_definition_and_instance_validation() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "items": {"type": "array", "items": {"type": "boolean"}},
        },
        "required": ["name"],
    }
    validate_schema_definition(schema)
    validate_instance({"name": "ok", "count": 2, "items": [True]}, schema)
    for value, match in (
        ({}, "missing required"),
        ({"name": "x"}, "at least 2"),
        ({"name": "ok", "count": True}, "integer"),
        ({"name": "ok", "extra": 1}, "unexpected property"),
    ):
        with pytest.raises(SchemaValidationError, match=match):
            validate_instance(value, schema)
    with pytest.raises(SchemaDefinitionError, match="unsupported keyword"):
        validate_schema_definition({"type": "object", "oneOf": []})


def test_effect_and_route_are_centralized() -> None:
    assert tool_effect(definition()) == "read"
    assert tool_effect(
        definition(annotations={"readOnlyHint": False, "destructiveHint": False})
    ) == "write"
    assert tool_effect(
        definition(annotations={"readOnlyHint": False, "destructiveHint": True})
    ) == "destructive"
    assert action_route(definition("safe_tool-1")) == "safe_tool-1"


def test_executor_validates_and_preserves_domain_errors() -> None:
    calls: list[dict[str, Any]] = []

    def handler(arguments: Any) -> dict[str, Any]:
        calls.append(dict(arguments))
        if arguments.get("name") == "domain":
            raise ToolServiceError("domain failed", code="DOMAIN", retryable=True)
        return {"ok": True}

    tool = definition(
        schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
        handler=handler,
    )
    registry = ToolRegistry((tool,))
    executor = ToolExecutor(registry)
    invalid = executor.execute(tool, {})
    assert invalid.failure is not None
    assert invalid.failure.category == "schema_error"
    assert calls == []
    failed = executor.execute(tool, {"name": "domain"})
    assert failed.failure is not None
    assert (failed.failure.code, failed.failure.retryable) == ("DOMAIN", True)
    succeeded = executor.execute(tool, {"name": "ok"})
    assert succeeded.success and succeeded.payload == {"ok": True}


@pytest.mark.parametrize(
    ("exception", "category"),
    (
        (AuthError("auth"), "backend_auth_error"),
        (AmbiguousWriteError("ambiguous"), "ambiguous_write"),
        (ApiError("upstream"), "upstream_error"),
        (ApiError("timeout", timed_out=True), "upstream_timeout"),
        (JoplinSyncError("expected"), "expected_error"),
        (ToolServiceError("partial", code="PARTIAL_WRITE"), "partial_write"),
        (RuntimeError("unexpected"), "internal_error"),
    ),
)
def test_executor_classifies_expected_failures(
    exception: Exception, category: str
) -> None:
    def handler(_arguments: Any) -> dict[str, Any]:
        raise exception

    tool = definition(handler=handler)
    execution = ToolExecutor(ToolRegistry((tool,))).execute(tool, {})
    assert execution.failure is not None
    assert execution.failure.category == category
