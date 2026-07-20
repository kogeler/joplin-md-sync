"""Transport-neutral execution and expected error classification for Joplin tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from joplin_md_sync.api import AmbiguousWriteError
from joplin_md_sync.errors import ApiError, AuthError, JoplinSyncError
from joplin_md_sync.mcp_service import ToolServiceError
from joplin_md_sync.tool_registry import JsonObject, ToolDefinition, ToolRegistry
from joplin_md_sync.tool_schema import SchemaValidationError, validate_instance

log = logging.getLogger("joplin_md_sync.tools")

FailureClass = Literal[
    "schema_error",
    "domain_error",
    "backend_auth_error",
    "ambiguous_write",
    "partial_write",
    "upstream_error",
    "upstream_timeout",
    "expected_error",
    "internal_error",
]


@dataclass(frozen=True)
class ToolFailure:
    code: str
    message: str
    retryable: bool
    category: FailureClass
    details: object = None


@dataclass(frozen=True)
class ToolExecution:
    tool: ToolDefinition
    payload: JsonObject | None = None
    failure: ToolFailure | None = None

    @property
    def success(self) -> bool:
        return self.failure is None


class ToolExecutor:
    """Validate arguments, invoke handlers directly, and classify failures."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, tool: ToolDefinition, arguments: dict[str, Any]) -> ToolExecution:
        try:
            validate_instance(arguments, tool.input_schema)
        except SchemaValidationError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    "INVALID_ARGUMENT", str(exc), False, "schema_error"
                ),
            )
        try:
            return ToolExecution(tool, payload=tool.handler(arguments))
        except ToolServiceError as exc:
            category: FailureClass = (
                "partial_write" if exc.code.startswith("PARTIAL_") else "domain_error"
            )
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    exc.code, str(exc), exc.retryable, category, exc.details
                ),
            )
        except AuthError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(exc.code, str(exc), False, "backend_auth_error"),
            )
        except AmbiguousWriteError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    "AMBIGUOUS_WRITE", str(exc), False, "ambiguous_write"
                ),
            )
        except ApiError as exc:
            upstream_category: FailureClass = (
                "upstream_timeout" if exc.timed_out else "upstream_error"
            )
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    exc.code,
                    str(exc),
                    exc.status is None or exc.status >= 500,
                    upstream_category,
                ),
            )
        except JoplinSyncError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    exc.code, str(exc), False, "expected_error", exc.details
                ),
            )
        except Exception:
            log.exception("unexpected tool failure: %s", tool.name)
            return ToolExecution(
                tool,
                failure=ToolFailure(
                    "INTERNAL_ERROR", "internal tool failure", False, "internal_error"
                ),
            )
