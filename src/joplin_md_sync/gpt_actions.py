"""Authenticated GPT Actions transport over the shared tool executor."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import math
import os
import secrets
import stat
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from joplin_md_sync.gpt_openapi import ACTION_PATH_PREFIX
from joplin_md_sync.tool_executor import ToolExecution, ToolExecutor
from joplin_md_sync.tool_registry import ToolDefinition, ToolRegistry, tool_effect

log = logging.getLogger("joplin_md_sync.gpt_actions")

ACTIONS_NAMESPACE = "/api/gpt/v1"
HARD_MAX_PAYLOAD_CHARS = 99_999
DEFAULT_MAX_REQUEST_BYTES = 95_000
DEFAULT_MAX_RESPONSE_CHARS = 95_000
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_RATE_LIMIT_PER_MINUTE = 60

JsonObject = dict[str, Any]


class ActionsTokenSource:
    """Required secret-file source, reloaded on every authentication attempt."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.read()

    def read(self) -> str:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            path_info = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(path_info.st_mode):
                raise ValueError(
                    f"GPT Actions token file must be a regular file: {self.path}"
                )
            descriptor = os.open(self.path, flags)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(
                    f"GPT Actions token file must be a regular file: {self.path}"
                )
            if os.name == "posix" and info.st_mode & 0o077:
                raise ValueError(
                    "GPT Actions token file must not be accessible by group or others: "
                    f"{self.path}"
                )
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                token = handle.read().strip()
        except ValueError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except (OSError, UnicodeError) as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ValueError(f"GPT Actions token file cannot be read: {self.path}: {exc}") from None
        if not token:
            raise ValueError(f"GPT Actions token file is empty: {self.path}")
        try:
            decoded = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
        except (UnicodeEncodeError, ValueError, binascii.Error):
            raise ValueError("GPT Actions token must use URL-safe base64 encoding") from None
        if len(decoded) < 32:
            raise ValueError(
                "GPT Actions token must encode at least 32 random bytes"
            )
        return token

    def accepts(self, authorization: str | None) -> bool:
        if authorization is None:
            return False
        scheme, separator, supplied = authorization.partition(" ")
        if separator != " " or scheme != "Bearer" or not supplied or supplied != supplied.strip():
            return False
        try:
            expected = self.read()
        except ValueError:
            log.error("GPT Actions token reload failed")
            return False
        return hmac.compare_digest(supplied, expected)


def validate_distinct_actions_token(
    actions_token: str, *, joplin_token: str, mcp_token: str | None
) -> None:
    if hmac.compare_digest(actions_token, joplin_token):
        raise ValueError("GPT Actions token must differ from the Joplin token")
    if mcp_token is not None and hmac.compare_digest(actions_token, mcp_token):
        raise ValueError("GPT Actions token must differ from the MCP token")


class TokenBucket:
    """Thread-safe bounded token bucket used for authenticated requests."""

    def __init__(self, per_minute: int) -> None:
        if per_minute <= 0:
            raise ValueError("rate limit must be positive")
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)
        self._rate = float(per_minute) / 60.0
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._updated) * self._rate,
            )
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True, 0
            return False, max(1, math.ceil((1 - self._tokens) / self._rate))


@dataclass(frozen=True)
class ActionsConfig:
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE

    def __post_init__(self) -> None:
        if not 2 <= self.max_request_bytes <= HARD_MAX_PAYLOAD_CHARS:
            raise ValueError(
                f"GPT Actions request limit must be between 2 and {HARD_MAX_PAYLOAD_CHARS}"
            )
        if not 256 <= self.max_response_chars <= HARD_MAX_PAYLOAD_CHARS:
            raise ValueError(
                "GPT Actions response limit must be between 256 and "
                f"{HARD_MAX_PAYLOAD_CHARS}"
            )
        if self.max_concurrency <= 0:
            raise ValueError("GPT Actions max concurrency must be positive")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("GPT Actions rate limit must be positive")


class GptActionsTransport:
    """Route authenticated HTTP calls to the shared registry and executor."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        token_source: ActionsTokenSource,
        *,
        config: ActionsConfig | None = None,
    ) -> None:
        config = config or ActionsConfig()
        self.registry = registry
        self.executor = executor
        self.token_source = token_source
        self.config = config
        self._capacity = threading.BoundedSemaphore(config.max_concurrency)
        self._rate_limit = TokenBucket(config.rate_limit_per_minute)
        self._auth_failure_limit = TokenBucket(max(10, config.rate_limit_per_minute))

    def handles(self, path: str) -> bool:
        clean = urllib.parse.urlsplit(path).path
        return clean == ACTIONS_NAMESPACE or clean.startswith(f"{ACTIONS_NAMESPACE}/")

    def handle(self, handler: Any, method: str) -> bool:
        if not self.handles(handler.path):
            return False
        request_id = secrets.token_hex(16)
        started = time.monotonic()
        request_size = 0
        if not self._capacity.acquire(blocking=False):
            self._complete(
                handler,
                HTTPStatus.SERVICE_UNAVAILABLE,
                self._error("CAPACITY_EXHAUSTED", "The service is busy.", True, request_id),
                request_id,
                started,
                request_size,
                result_class="capacity_exhausted",
                retry_after=1,
            )
            return True
        try:
            if method == "POST":
                raw_length = handler.headers.get("Content-Length")
                try:
                    length = int(raw_length or "")
                except ValueError:
                    length = -1
                if length < 0:
                    handler.close_connection = True
                    self._complete(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        self._error(
                            "INVALID_HTTP_REQUEST",
                            "A valid Content-Length is required.",
                            False,
                            request_id,
                        ),
                        request_id,
                        started,
                        request_size,
                        result_class="invalid_http",
                    )
                    return True
                request_size = length
                if length > self.config.max_request_bytes or length > HARD_MAX_PAYLOAD_CHARS:
                    handler.close_connection = True
                    self._complete(
                        handler,
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        self._error(
                            "REQUEST_TOO_LARGE",
                            "The request body is too large.",
                            False,
                            request_id,
                        ),
                        request_id,
                        started,
                        request_size,
                        result_class="request_too_large",
                    )
                    return True

            if not self.token_source.accepts(handler.headers.get("Authorization")):
                allowed, retry_after = self._auth_failure_limit.consume()
                if not allowed:
                    self._complete(
                        handler,
                        HTTPStatus.TOO_MANY_REQUESTS,
                        self._error(
                            "RATE_LIMITED",
                            "Too many authentication attempts.",
                            True,
                            request_id,
                        ),
                        request_id,
                        started,
                        request_size,
                        result_class="auth_rate_limited",
                        retry_after=retry_after,
                    )
                    return True
                self._complete(
                    handler,
                    HTTPStatus.UNAUTHORIZED,
                    self._error(
                        "UNAUTHORIZED",
                        "Authentication is required.",
                        False,
                        request_id,
                    ),
                    request_id,
                    started,
                    request_size,
                    result_class="unauthorized",
                    authenticate=True,
                )
                return True

            allowed, retry_after = self._rate_limit.consume()
            if not allowed:
                self._complete(
                    handler,
                    HTTPStatus.TOO_MANY_REQUESTS,
                    self._error(
                        "RATE_LIMITED", "The request rate limit was exceeded.", True, request_id
                    ),
                    request_id,
                    started,
                    request_size,
                    result_class="rate_limited",
                    retry_after=retry_after,
                )
                return True

            clean_path = urllib.parse.urlsplit(handler.path).path
            prefix = f"{ACTION_PATH_PREFIX}/"
            route = clean_path.removeprefix(prefix) if clean_path.startswith(prefix) else ""
            tool = (
                self.registry.get_by_action_route(route)
                if route and "/" not in route
                else None
            )
            if tool is None:
                self._complete(
                    handler,
                    HTTPStatus.NOT_FOUND,
                    self._error(
                        "ACTION_NOT_FOUND", "The requested Action does not exist.", False, request_id
                    ),
                    request_id,
                    started,
                    request_size,
                    result_class="not_found",
                )
                return True
            if method != "POST":
                self._complete(
                    handler,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    self._error(
                        "METHOD_NOT_ALLOWED", "Only POST is supported.", False, request_id
                    ),
                    request_id,
                    started,
                    request_size,
                    tool=tool,
                    result_class="method_not_allowed",
                    allow="POST",
                )
                return True
            if handler.headers.get_content_type() != "application/json":
                self._complete(
                    handler,
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    self._error(
                        "UNSUPPORTED_MEDIA_TYPE",
                        "Content-Type must be application/json.",
                        False,
                        request_id,
                    ),
                    request_id,
                    started,
                    request_size,
                    tool=tool,
                    result_class="unsupported_media_type",
                )
                return True

            try:
                raw = handler.rfile.read(request_size)
                arguments = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"invalid JSON constant: {value}")
                    ),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._complete(
                    handler,
                    HTTPStatus.BAD_REQUEST,
                    self._error("INVALID_JSON", "The request body is invalid JSON.", False, request_id),
                    request_id,
                    started,
                    request_size,
                    tool=tool,
                    result_class="invalid_json",
                )
                return True
            if not isinstance(arguments, dict) or any(
                not isinstance(key, str) for key in arguments
            ):
                self._complete(
                    handler,
                    HTTPStatus.BAD_REQUEST,
                    self._error("INVALID_JSON", "The request body must be one JSON object.", False, request_id),
                    request_id,
                    started,
                    request_size,
                    tool=tool,
                    result_class="invalid_json",
                )
                return True

            handler_started = time.monotonic()
            execution = self.executor.execute(tool, arguments)
            handler_duration = time.monotonic() - handler_started
            status, payload, result_class = self._execution_response(execution, request_id)
            rendered = self._render(payload)
            if len(rendered) > self.config.max_response_chars or len(rendered) > HARD_MAX_PAYLOAD_CHARS:
                if execution.success and tool_effect(tool) != "read":
                    status = HTTPStatus.OK
                    result_class = "success_result_omitted"
                    payload = {
                        "success": True,
                        "result": {
                            "result_omitted": True,
                            "result_omitted_reason": "RESULT_TOO_LARGE",
                        },
                        "request_id": request_id,
                    }
                else:
                    status = HTTPStatus.BAD_GATEWAY
                    result_class = "result_too_large"
                    payload = self._error(
                        "RESULT_TOO_LARGE",
                        "The tool result is too large for GPT Actions.",
                        False,
                        request_id,
                    )
            self._complete(
                handler,
                status,
                payload,
                request_id,
                started,
                request_size,
                tool=tool,
                result_class=result_class,
                handler_duration=handler_duration,
            )
            return True
        finally:
            self._capacity.release()

    @staticmethod
    def _error(
        code: str, message: str, retryable: bool, request_id: str
    ) -> JsonObject:
        return {
            "success": False,
            "error": {"code": code, "message": message, "retryable": retryable},
            "request_id": request_id,
        }

    def _execution_response(
        self, execution: ToolExecution, request_id: str
    ) -> tuple[HTTPStatus, JsonObject, str]:
        if execution.success:
            assert execution.payload is not None
            return (
                HTTPStatus.OK,
                {"success": True, "result": execution.payload, "request_id": request_id},
                "success",
            )
        failure = execution.failure
        assert failure is not None
        if failure.category in {"schema_error", "domain_error"}:
            return (
                HTTPStatus.UNPROCESSABLE_ENTITY,
                self._error(failure.code, failure.message, failure.retryable, request_id),
                failure.category,
            )
        if failure.category == "backend_auth_error":
            return (
                HTTPStatus.BAD_GATEWAY,
                self._error(
                    "UPSTREAM_AUTH_FAILED",
                    "The private Joplin service rejected its credential.",
                    False,
                    request_id,
                ),
                failure.category,
            )
        if failure.category == "ambiguous_write":
            return (
                HTTPStatus.BAD_GATEWAY,
                self._error(
                    failure.code,
                    "The write outcome is ambiguous; inspect Joplin before trying again.",
                    False,
                    request_id,
                ),
                failure.category,
            )
        if failure.category == "partial_write":
            return (
                HTTPStatus.BAD_GATEWAY,
                self._error(
                    failure.code,
                    "The write partially completed; inspect Joplin before trying again.",
                    False,
                    request_id,
                ),
                failure.category,
            )
        if failure.category == "upstream_error":
            status = (
                HTTPStatus.SERVICE_UNAVAILABLE
                if failure.retryable
                else HTTPStatus.BAD_GATEWAY
            )
            return (
                status,
                self._error(
                    failure.code,
                    "The private Joplin service request failed.",
                    failure.retryable,
                    request_id,
                ),
                failure.category,
            )
        if failure.category == "upstream_timeout":
            return (
                HTTPStatus.GATEWAY_TIMEOUT,
                self._error(
                    failure.code,
                    "The private Joplin service request timed out.",
                    True,
                    request_id,
                ),
                failure.category,
            )
        if failure.category == "expected_error":
            return (
                HTTPStatus.BAD_GATEWAY,
                self._error(
                    failure.code, "The tool operation failed.", False, request_id
                ),
                failure.category,
            )
        return (
            HTTPStatus.INTERNAL_SERVER_ERROR,
            self._error("INTERNAL_ERROR", "The tool operation failed.", False, request_id),
            failure.category,
        )

    @staticmethod
    def _render(payload: JsonObject) -> str:
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    def _complete(
        self,
        handler: Any,
        status: HTTPStatus,
        payload: JsonObject,
        request_id: str,
        started: float,
        request_size: int,
        *,
        result_class: str,
        tool: ToolDefinition | None = None,
        handler_duration: float = 0,
        retry_after: int | None = None,
        authenticate: bool = False,
        allow: str | None = None,
    ) -> None:
        rendered = self._render(payload)
        body = rendered.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Request-ID", request_id)
        if authenticate:
            handler.send_header(
                "WWW-Authenticate", 'Bearer realm="joplin-md-sync-gpt-actions"'
            )
        if retry_after is not None:
            handler.send_header("Retry-After", str(retry_after))
        if allow is not None:
            handler.send_header("Allow", allow)
        handler.end_headers()
        event = {
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "effect": tool_effect(tool) if tool is not None else None,
            "handler_duration_ms": round(handler_duration * 1000, 3),
            "http_status": int(status),
            "request_bytes": request_size,
            "request_id": request_id,
            "response_bytes": 0 if handler.command == "HEAD" else len(body),
            "response_chars": len(rendered),
            "result_class": result_class,
            "tool": tool.name if tool is not None else None,
            "transport": "gpt_actions",
        }
        log.info("gpt_actions_request %s", json.dumps(event, sort_keys=True))
        if handler.command == "HEAD":
            return
        try:
            handler.wfile.write(body)
        except OSError:
            log.warning("GPT Actions client disconnected request_id=%s", request_id)
