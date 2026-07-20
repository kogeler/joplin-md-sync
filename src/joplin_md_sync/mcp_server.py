"""Dependency-free MCP Streamable HTTP server for direct Joplin access."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joplin_md_sync import __version__
from joplin_md_sync.errors import AuthError
from joplin_md_sync.mcp_service import JoplinMcpService
from joplin_md_sync.tool_executor import ToolExecutor
from joplin_md_sync.tool_registry import ToolRegistry, build_tool_registry

if TYPE_CHECKING:
    from joplin_md_sync.gpt_actions import GptActionsTransport

log = logging.getLogger("joplin_md_sync.mcp")

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SUPPORTED_VERSIONS = frozenset({"2025-03-26", MCP_PROTOCOL_VERSION})
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
MAX_REQUEST_BYTES = 16 * 1024 * 1024

JsonObject = dict[str, Any]


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpDispatcher:
    """JSON-RPC/MCP adapter over the shared tool registry and executor."""

    def __init__(
        self, service: JoplinMcpService, *, registry: ToolRegistry | None = None
    ) -> None:
        self.registry = registry or build_tool_registry(service)
        self.executor = ToolExecutor(self.registry)

    @staticmethod
    def _tool_result(payload: JsonObject, *, is_error: bool = False) -> JsonObject:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        }

    @classmethod
    def _tool_error(
        cls, message: str, *, code: str, retryable: bool, details: object = None
    ) -> JsonObject:
        error: JsonObject = {"code": code, "message": message, "retryable": retryable}
        if details is not None:
            error["details"] = details
        return cls._tool_result({"error": error}, is_error=True)

    def _call_tool(self, params: object) -> JsonObject:
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call params must be an object")
        name = params.get("name")
        tool = self.registry.get(name) if isinstance(name, str) else None
        if tool is None:
            raise RpcError(-32602, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict) or any(
            not isinstance(key, str) for key in arguments
        ):
            raise RpcError(-32602, "tool arguments must be an object with string keys")
        execution = self.executor.execute(tool, arguments)
        if execution.success:
            assert execution.payload is not None
            return self._tool_result(execution.payload)
        failure = execution.failure
        assert failure is not None
        return self._tool_error(
            failure.message,
            code=failure.code,
            retryable=failure.retryable,
            details=failure.details,
        )

    def dispatch(self, message: object) -> JsonObject | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RpcError(-32600, "Invalid JSON-RPC request")
        method = message.get("method")
        if method is None and ("result" in message or "error" in message):
            return None
        if not isinstance(method, str):
            raise RpcError(-32600, "JSON-RPC method must be a string")

        has_id = "id" in message
        request_id = message.get("id")
        try:
            if method == "initialize":
                params = message.get("params")
                if not isinstance(params, dict):
                    raise RpcError(-32602, "initialize params must be an object")
                requested = params.get("protocolVersion")
                version = (
                    requested
                    if requested in MCP_SUPPORTED_VERSIONS
                    else MCP_PROTOCOL_VERSION
                )
                result: JsonObject = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "joplin-md-sync",
                        "title": "Joplin Markdown Sync MCP",
                        "version": __version__,
                    },
                    "instructions": (
                        "Read and modify Joplin notes through the Joplin Data API. "
                        "Delete moves notes to trash and never permanently deletes them."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                params = message.get("params", {})
                if not isinstance(params, dict):
                    raise RpcError(-32602, "tools/list params must be an object")
                cursor = params.get("cursor")
                if cursor not in (None, ""):
                    raise RpcError(-32602, "tools/list cursor is not supported")
                result = {
                    "tools": [tool.to_mcp_json() for tool in self.registry.definitions]
                }
            elif method == "tools/call":
                result = self._call_tool(message.get("params"))
            elif method in {
                "notifications/initialized",
                "notifications/cancelled",
                "notifications/progress",
            }:
                return None
            else:
                if not has_id:
                    return None
                raise RpcError(-32601, f"Method not found: {method}")
        except RpcError:
            if not has_id:
                return None
            raise
        if not has_id:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


class BearerTokenSource:
    """Optional MCP shared-secret source, reloaded to support token rotation."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            self.read()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def read(self) -> str:
        if self.path is None:
            return ""
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AuthError(f"MCP auth token file cannot be read: {self.path}: {exc}") from None
        if not token:
            raise AuthError(f"MCP auth token file is empty: {self.path}")
        return token

    def accepts(self, authorization: str | None) -> bool:
        if not self.enabled:
            return True
        if authorization is None or not authorization.startswith("Bearer "):
            return False
        supplied = authorization.removeprefix("Bearer ").strip()
        return bool(supplied) and hmac.compare_digest(supplied, self.read())


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class McpHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        dispatcher: McpDispatcher,
        *,
        endpoint: str = DEFAULT_MCP_PATH,
        token_source: BearerTokenSource | None = None,
        allowed_origins: frozenset[str] = frozenset(),
        actions_transport: GptActionsTransport | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.endpoint = endpoint
        self.token_source = token_source or BearerTokenSource(None)
        self.allowed_origins = allowed_origins
        self.actions_transport = actions_transport
        super().__init__(address, McpRequestHandler)


class McpRequestHandler(BaseHTTPRequestHandler):
    server: McpHttpServer
    protocol_version = "HTTP/1.1"
    server_version = f"joplin-md-sync/{__version__}"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug("HTTP %s - %s", self.address_string(), fmt % args)

    def _empty(self, status: HTTPStatus, *, allow: str | None = None) -> None:
        self.send_response(status)
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status: HTTPStatus, payload: JsonObject) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _rpc_error(
        self, status: HTTPStatus, code: int, message: str, *, request_id: object = None
    ) -> None:
        self._json(
            status,
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        )

    def _authorized(self) -> bool:
        try:
            accepted = self.server.token_source.accepts(self.headers.get("Authorization"))
        except AuthError as exc:
            log.error("%s", exc)
            accepted = False
        if accepted:
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Bearer realm="joplin-md-sync-mcp"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        normalized = origin.rstrip("/")
        if normalized in self.server.allowed_origins:
            return True
        hostname = urllib.parse.urlsplit(origin).hostname or ""
        raw_bound_host = self.server.server_address[0]
        bound_host = (
            raw_bound_host.decode("ascii", "replace")
            if isinstance(raw_bound_host, bytes | bytearray)
            else raw_bound_host
        )
        return is_loopback_host(hostname) and is_loopback_host(bound_host)

    def _common_checks(self) -> bool:
        if urllib.parse.urlsplit(self.path).path != self.server.endpoint:
            self._empty(HTTPStatus.NOT_FOUND)
            return False
        if not self._origin_allowed():
            self._empty(HTTPStatus.FORBIDDEN)
            return False
        return self._authorized()

    def _handle_action(self, method: str) -> bool:
        transport = self.server.actions_transport
        return transport is not None and transport.handle(self, method)

    def _handle_health(self) -> bool:
        path = urllib.parse.urlsplit(self.path).path
        if path not in {"/healthz", "/readyz"}:
            return False
        if not is_loopback_host(str(self.client_address[0])):
            self._empty(HTTPStatus.NOT_FOUND)
            return True
        self._json(HTTPStatus.OK, {"ok": True})
        return True

    def do_GET(self) -> None:
        if self._handle_action("GET") or self._handle_health():
            return
        if self._common_checks():
            self._empty(HTTPStatus.METHOD_NOT_ALLOWED, allow="POST")

    def do_DELETE(self) -> None:
        if self._handle_action("DELETE"):
            return
        if self._common_checks():
            self._empty(HTTPStatus.METHOD_NOT_ALLOWED, allow="POST")

    def do_HEAD(self) -> None:
        if not self._handle_action("HEAD"):
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('HEAD')")

    def do_PUT(self) -> None:
        if not self._handle_action("PUT"):
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('PUT')")

    def do_PATCH(self) -> None:
        if not self._handle_action("PATCH"):
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('PATCH')")

    def do_OPTIONS(self) -> None:
        if not self._handle_action("OPTIONS"):
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('OPTIONS')")

    def do_TRACE(self) -> None:
        if not self._handle_action("TRACE"):
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method ('TRACE')")

    def do_POST(self) -> None:
        if self._handle_action("POST"):
            return
        if not self._common_checks():
            return
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            self._rpc_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, -32600, "Content-Type must be application/json")
            return
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            self._rpc_error(
                HTTPStatus.NOT_ACCEPTABLE,
                -32600,
                "Accept must include application/json and text/event-stream",
            )
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            length = -1
        if length < 0:
            self._rpc_error(HTTPStatus.LENGTH_REQUIRED, -32600, "A valid Content-Length is required")
            return
        if length > MAX_REQUEST_BYTES:
            self._rpc_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, -32600, "Request body is too large")
            return
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._rpc_error(HTTPStatus.BAD_REQUEST, -32700, "Parse error")
            return

        method = message.get("method") if isinstance(message, dict) else None
        protocol_header = self.headers.get("MCP-Protocol-Version")
        if method != "initialize":
            effective_version = protocol_header or "2025-03-26"
            if effective_version not in MCP_SUPPORTED_VERSIONS:
                self._rpc_error(
                    HTTPStatus.BAD_REQUEST, -32600, "Unsupported MCP-Protocol-Version"
                )
                return
        mirrored_method = self.headers.get("Mcp-Method")
        if mirrored_method is not None and mirrored_method != method:
            self._rpc_error(HTTPStatus.BAD_REQUEST, -32600, "Mcp-Method header mismatch")
            return
        try:
            response = self.server.dispatcher.dispatch(message)
        except RpcError as exc:
            request_id = message.get("id") if isinstance(message, dict) else None
            error: JsonObject = {"code": exc.code, "message": str(exc)}
            if exc.data is not None:
                error["data"] = exc.data
            self._json(
                HTTPStatus.OK,
                {"jsonrpc": "2.0", "id": request_id, "error": error},
            )
            return
        if response is None:
            self._empty(HTTPStatus.ACCEPTED)
        else:
            self._json(HTTPStatus.OK, response)


def serve_mcp_http(
    dispatcher: McpDispatcher,
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    endpoint: str = DEFAULT_MCP_PATH,
    auth_token_file: Path | None = None,
    allowed_origins: frozenset[str] = frozenset(),
    actions_transport: GptActionsTransport | None = None,
) -> None:
    """Run the foreground MCP HTTP process until interrupted or terminated."""
    server = McpHttpServer(
        (host, port),
        dispatcher,
        endpoint=endpoint,
        token_source=BearerTokenSource(auth_token_file),
        allowed_origins=allowed_origins,
        actions_transport=actions_transport,
    )
    log.info("MCP server listening on http://%s:%d%s", host, port, endpoint)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("MCP server interrupted")
    finally:
        server.server_close()
