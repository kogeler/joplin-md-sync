"""Transport-neutral execution and expected error classification for Joplin tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from joplin_md_sync.api import AmbiguousWriteError
from joplin_md_sync.errors import ApiError, AuthError, JoplinSyncError
from joplin_md_sync.mcp_service import ToolServiceError
from joplin_md_sync.tool_registry import JsonObject, ToolDefinition, ToolRegistry
from joplin_md_sync.tool_schema import SchemaValidationError, validate_instance

log = logging.getLogger("joplin_md_sync.tools")


@dataclass(frozen=True)
class ToolFailure:
    code: str
    message: str
    retryable: bool
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
                failure=ToolFailure("INVALID_ARGUMENT", str(exc), False),
            )
        try:
            return ToolExecution(tool, payload=tool.handler(arguments))
        except ToolServiceError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(exc.code, str(exc), exc.retryable, exc.details),
            )
        except AuthError as exc:
            return ToolExecution(tool, failure=ToolFailure(exc.code, str(exc), False))
        except AmbiguousWriteError as exc:
            return ToolExecution(tool, failure=ToolFailure("AMBIGUOUS_WRITE", str(exc), False))
        except ApiError as exc:
            return ToolExecution(
                tool,
                failure=ToolFailure(exc.code, str(exc), exc.status is None or exc.status >= 500),
            )
        except JoplinSyncError as exc:
            return ToolExecution(tool, failure=ToolFailure(exc.code, str(exc), False, exc.details))
        except Exception:
            log.exception("unexpected tool failure: %s", tool.name)
            return ToolExecution(
                tool, failure=ToolFailure("INTERNAL_ERROR", "internal tool failure", False)
            )
