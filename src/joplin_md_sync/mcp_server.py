"""Dependency-free MCP Streamable HTTP server for direct Joplin access."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from joplin_md_sync import __version__
from joplin_md_sync.api import AmbiguousWriteError
from joplin_md_sync.errors import ApiError, AuthError, JoplinSyncError
from joplin_md_sync.mcp_service import JoplinMcpService, ToolServiceError

log = logging.getLogger("joplin_md_sync.mcp")

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SUPPORTED_VERSIONS = frozenset({"2025-03-26", MCP_PROTOCOL_VERSION})
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
MAX_REQUEST_BYTES = 16 * 1024 * 1024

JsonObject = dict[str, Any]
ToolHandler = Callable[[Mapping[str, object]], JsonObject]


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class McpTool:
    name: str
    title: str
    description: str
    input_schema: JsonObject
    annotations: JsonObject
    handler: ToolHandler

    def to_json(self) -> JsonObject:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }


def _object_schema(properties: JsonObject, *, required: tuple[str, ...] = ()) -> JsonObject:
    schema: JsonObject = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_NOTE_ID = {"type": "string", "minLength": 1, "description": "Joplin note ID."}
_NOTEBOOK_ID = {"type": "string", "minLength": 1, "description": "Joplin notebook ID."}
_TAG_ID = {"type": "string", "minLength": 1, "description": "Joplin tag ID."}
_RESOURCE_ID = {"type": "string", "minLength": 1, "description": "Joplin resource ID."}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}
_TAGS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Complete replacement tag set; values are normalized by Joplin.",
}
_EDITABLE_PROPERTIES: JsonObject = {
    "title": {"type": "string", "description": "Note title."},
    "body": {"type": "string", "description": "Markdown note body."},
    "body_html": {"type": "string", "description": "HTML note body converted by Joplin."},
    "base_url": {"type": "string", "description": "Base URL for relative links in body_html."},
    "image_data_url": {"type": "string", "description": "Image data URL attached by Joplin."},
    "crop_rect": {"type": "string", "description": "Joplin image crop rectangle JSON."},
    "parent_id": {"type": "string", "description": "Destination notebook ID."},
    "tags": _TAGS,
    "author": {"type": "string"},
    "source_url": {"type": "string"},
    "source": {"type": "string"},
    "source_application": {"type": "string"},
    "application_data": {"type": "string"},
    "user_data": {"type": "string"},
    "is_todo": {"type": "boolean"},
    "todo_due": {"type": "integer", "minimum": 0},
    "todo_completed": {"type": "integer", "minimum": 0},
    "user_created_time": {"type": "integer", "minimum": 0},
    "user_updated_time": {"type": "integer", "minimum": 0},
    "latitude": {"type": "number"},
    "longitude": {"type": "number"},
    "altitude": {"type": "number"},
    "order": {"type": "number"},
    "markup_language": {"type": "integer", "minimum": 0},
}
_ATTACHMENTS = {
    "type": "array",
    "description": "Binary resources to upload and append to the note as Joplin Markdown links.",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "filename": {"type": "string", "minLength": 1},
            "mime": {"type": "string", "minLength": 1},
            "title": {"type": "string"},
            "alt_text": {"type": "string"},
            "content_base64": {
                "type": "string",
                "minLength": 1,
                "description": "Base64 content; decoded size is limited to 10 MiB per resource.",
            },
        },
        "required": ["filename", "mime", "content_base64"],
    },
}


class McpDispatcher:
    """JSON-RPC/MCP lifecycle and tool registry, with no HTTP assumptions."""

    def __init__(self, service: JoplinMcpService) -> None:
        self._service = service
        self._tools = {tool.name: tool for tool in self._build_tools()}

    def _build_tools(self) -> tuple[McpTool, ...]:
        read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
        write = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
        destructive = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        }
        permanent_destructive = {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
        return (
            McpTool(
                "joplin_list_notebooks",
                "List Joplin notebooks",
                "List notebooks and their parent relationships. Use this to obtain parent_id values.",
                _object_schema(
                    {
                        "limit": _LIMIT,
                        "include_deleted": {"type": "boolean", "default": False},
                    }
                ),
                read_only,
                self._service.list_notebooks,
            ),
            McpTool(
                "joplin_get_notebook",
                "Read a Joplin notebook",
                "Read notebook metadata, including parent, icon, timestamps, and trash state.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                read_only,
                self._get_notebook,
            ),
            McpTool(
                "joplin_create_notebook",
                "Create a Joplin notebook",
                "Create a root or nested notebook with metadata.",
                _object_schema(
                    {
                        "title": {"type": "string", "minLength": 1},
                        "parent_id": {"type": "string"},
                        "icon": {"type": "string"},
                        "user_created_time": {"type": "integer", "minimum": 0},
                        "user_updated_time": {"type": "integer", "minimum": 0},
                    },
                    required=("title",),
                ),
                write,
                self._service.create_notebook,
            ),
            McpTool(
                "joplin_update_notebook",
                "Update a Joplin notebook",
                "Rename, move, or update metadata of a notebook.",
                _object_schema(
                    {
                        "notebook_id": _NOTEBOOK_ID,
                        "title": {"type": "string", "minLength": 1},
                        "parent_id": {"type": "string"},
                        "icon": {"type": "string"},
                        "user_created_time": {"type": "integer", "minimum": 0},
                        "user_updated_time": {"type": "integer", "minimum": 0},
                    },
                    required=("notebook_id",),
                ),
                write,
                self._service.update_notebook,
            ),
            McpTool(
                "joplin_delete_notebook",
                "Move a Joplin notebook to trash",
                "Move a notebook to Joplin trash. Permanent deletion is not exposed.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                destructive,
                self._delete_notebook,
            ),
            McpTool(
                "joplin_restore_notebook",
                "Restore a Joplin notebook",
                "Restore a trashed notebook by clearing its deleted state.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                write,
                self._restore_notebook,
            ),
            McpTool(
                "joplin_list_notebook_notes",
                "List notes in a Joplin notebook",
                "List notes directly contained in one notebook.",
                _object_schema(
                    {
                        "notebook_id": _NOTEBOOK_ID,
                        "limit": _LIMIT,
                        "include_deleted": {"type": "boolean", "default": False},
                        "include_conflicts": {"type": "boolean", "default": False},
                    },
                    required=("notebook_id",),
                ),
                read_only,
                self._service.list_notebook_notes,
            ),
            McpTool(
                "joplin_list_notes",
                "List Joplin notes",
                "List note IDs and core metadata without loading note bodies.",
                _object_schema(
                    {
                        "limit": _LIMIT,
                        "include_deleted": {"type": "boolean", "default": False},
                        "include_conflicts": {"type": "boolean", "default": False},
                    }
                ),
                read_only,
                self._service.list_notes,
            ),
            McpTool(
                "joplin_get_note",
                "Read a Joplin note",
                "Read Markdown body, tags, notebook, timestamps, todo fields, and source metadata.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                read_only,
                self._get_note,
            ),
            McpTool(
                "joplin_create_note",
                "Create a Joplin note",
                (
                    "Create a Markdown note with tags and metadata. Set parent_id for an "
                    "existing notebook, or notebook_title to find/create one. If neither is "
                    "given, the MCP Notes notebook is found or created."
                ),
                _object_schema(
                    {
                        **_EDITABLE_PROPERTIES,
                        "attachments": _ATTACHMENTS,
                        "notebook_title": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Notebook title to find or create when parent_id is omitted."
                            ),
                        },
                    },
                    required=("title",),
                ),
                write,
                self._service.create_note,
            ),
            McpTool(
                "joplin_update_note",
                "Update a Joplin note",
                "Update only supplied content or metadata fields; tags replace the complete tag set.",
                _object_schema({"note_id": _NOTE_ID, **_EDITABLE_PROPERTIES}, required=("note_id",)),
                write,
                self._service.update_note,
            ),
            McpTool(
                "joplin_delete_note",
                "Move a Joplin note to trash",
                "Move a note to Joplin trash. Permanent deletion is not exposed.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                destructive,
                self._delete_note,
            ),
            McpTool(
                "joplin_restore_note",
                "Restore a Joplin note",
                "Restore a trashed note by clearing its deleted state.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                write,
                self._restore_note,
            ),
            McpTool(
                "joplin_search_notes",
                "Search Joplin notes",
                "Run Joplin full-text search syntax and return relevance-ordered note metadata.",
                _object_schema(
                    {
                        "query": {"type": "string", "minLength": 1},
                        "limit": {**_LIMIT, "default": 50},
                    },
                    required=("query",),
                ),
                read_only,
                self._service.search_notes,
            ),
            McpTool(
                "joplin_list_tags",
                "List Joplin tags",
                "List tag IDs, titles, and timestamps.",
                _object_schema({"limit": _LIMIT}),
                read_only,
                self._service.list_tags,
            ),
            McpTool(
                "joplin_get_tag",
                "Read a Joplin tag",
                "Read one tag and its metadata.",
                _object_schema({"tag_id": _TAG_ID}, required=("tag_id",)),
                read_only,
                self._get_tag,
            ),
            McpTool(
                "joplin_create_tag",
                "Create a Joplin tag",
                "Create a tag, or return the case-insensitive title match if it exists.",
                _object_schema(
                    {"title": {"type": "string", "minLength": 1}}, required=("title",)
                ),
                write,
                self._create_tag,
            ),
            McpTool(
                "joplin_update_tag",
                "Rename a Joplin tag",
                "Update the title of a tag.",
                _object_schema(
                    {"tag_id": _TAG_ID, "title": {"type": "string", "minLength": 1}},
                    required=("tag_id", "title"),
                ),
                write,
                self._service.update_tag,
            ),
            McpTool(
                "joplin_delete_tag",
                "Delete a Joplin tag",
                "Permanently delete a tag and its note associations; Joplin has no tag trash.",
                _object_schema({"tag_id": _TAG_ID}, required=("tag_id",)),
                permanent_destructive,
                self._delete_tag,
            ),
            McpTool(
                "joplin_list_tag_notes",
                "List notes with a Joplin tag",
                "List notes associated with one tag.",
                _object_schema(
                    {"tag_id": _TAG_ID, "limit": _LIMIT}, required=("tag_id",)
                ),
                read_only,
                self._service.list_tag_notes,
            ),
            McpTool(
                "joplin_add_tag_to_note",
                "Add a Joplin tag to a note",
                "Attach one tag without replacing the note's other tags.",
                _object_schema(
                    {"tag_id": _TAG_ID, "note_id": _NOTE_ID},
                    required=("tag_id", "note_id"),
                ),
                write,
                self._service.add_tag_to_note,
            ),
            McpTool(
                "joplin_remove_tag_from_note",
                "Remove a Joplin tag from a note",
                "Detach one tag without changing the note's other tags.",
                _object_schema(
                    {"tag_id": _TAG_ID, "note_id": _NOTE_ID},
                    required=("tag_id", "note_id"),
                ),
                write,
                self._service.remove_tag_from_note,
            ),
            McpTool(
                "joplin_list_resources",
                "List Joplin resources",
                "List attachment metadata without downloading binary content.",
                _object_schema({"limit": _LIMIT}),
                read_only,
                self._service.list_resources,
            ),
            McpTool(
                "joplin_get_resource",
                "Read Joplin resource metadata",
                "Read attachment metadata without downloading binary content.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                read_only,
                self._get_resource,
            ),
            McpTool(
                "joplin_read_resource",
                "Read Joplin resource content",
                "Read attachment metadata and up to 10 MiB of content encoded as base64.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                read_only,
                self._read_resource,
            ),
            McpTool(
                "joplin_create_resource",
                "Create a Joplin resource",
                "Upload a binary attachment from base64 content using the Joplin multipart API.",
                _object_schema(
                    {
                        "filename": {"type": "string", "minLength": 1},
                        "mime": {"type": "string", "minLength": 1},
                        "title": {"type": "string"},
                        "content_base64": {"type": "string", "minLength": 1},
                    },
                    required=("filename", "mime", "content_base64"),
                ),
                write,
                self._service.create_resource,
            ),
            McpTool(
                "joplin_update_resource",
                "Update a Joplin resource",
                "Update attachment metadata and optionally replace its base64 binary content.",
                _object_schema(
                    {
                        "resource_id": _RESOURCE_ID,
                        "filename": {"type": "string", "minLength": 1},
                        "mime": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "content_base64": {"type": "string", "minLength": 1},
                    },
                    required=("resource_id",),
                ),
                write,
                self._service.update_resource,
            ),
            McpTool(
                "joplin_delete_resource",
                "Delete a Joplin resource",
                "Permanently delete an attachment; Joplin has no resource trash.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                permanent_destructive,
                self._delete_resource,
            ),
            McpTool(
                "joplin_list_note_resources",
                "List resources in a Joplin note",
                "List attachment metadata referenced by one note.",
                _object_schema(
                    {"note_id": _NOTE_ID, "limit": _LIMIT}, required=("note_id",)
                ),
                read_only,
                self._service.list_note_resources,
            ),
            McpTool(
                "joplin_list_resource_notes",
                "List notes using a Joplin resource",
                "List notes that reference one attachment.",
                _object_schema(
                    {"resource_id": _RESOURCE_ID, "limit": _LIMIT},
                    required=("resource_id",),
                ),
                read_only,
                self._service.list_resource_notes,
            ),
        )

    @staticmethod
    def _single_value(
        arguments: Mapping[str, object],
        name: str,
        handler: Callable[[object], JsonObject],
    ) -> JsonObject:
        unknown = set(arguments) - {name}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        return handler(arguments.get(name))

    def _get_notebook(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "notebook_id", self._service.get_notebook)

    def _delete_notebook(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "notebook_id", self._service.delete_notebook)

    def _restore_notebook(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "notebook_id", self._service.restore_notebook)

    def _get_note(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "note_id", self._service.get_note)

    def _delete_note(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "note_id", self._service.delete_note)

    def _restore_note(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "note_id", self._service.restore_note)

    def _get_tag(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "tag_id", self._service.get_tag)

    def _create_tag(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "title", self._service.create_tag)

    def _delete_tag(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "tag_id", self._service.delete_tag)

    def _get_resource(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "resource_id", self._service.get_resource)

    def _read_resource(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "resource_id", self._service.read_resource)

    def _delete_resource(self, arguments: Mapping[str, object]) -> JsonObject:
        return self._single_value(arguments, "resource_id", self._service.delete_resource)

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
        if not isinstance(name, str) or name not in self._tools:
            raise RpcError(-32602, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise RpcError(-32602, "tool arguments must be an object with string keys")
        try:
            payload = self._tools[name].handler(arguments)
            return self._tool_result(payload)
        except ToolServiceError as exc:
            return self._tool_error(
                str(exc), code=exc.code, retryable=exc.retryable, details=exc.details
            )
        except AuthError as exc:
            return self._tool_error(str(exc), code=exc.code, retryable=False)
        except AmbiguousWriteError as exc:
            return self._tool_error(str(exc), code="AMBIGUOUS_WRITE", retryable=False)
        except ApiError as exc:
            retryable = exc.status is None or exc.status >= 500
            return self._tool_error(str(exc), code=exc.code, retryable=retryable)
        except JoplinSyncError as exc:
            return self._tool_error(str(exc), code=exc.code, retryable=False, details=exc.details)
        except Exception:
            log.exception("unexpected MCP tool failure: %s", name)
            return self._tool_error(
                "internal tool failure", code="INTERNAL_ERROR", retryable=False
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
                version = requested if requested in MCP_SUPPORTED_VERSIONS else MCP_PROTOCOL_VERSION
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
                result = {"tools": [tool.to_json() for tool in self._tools.values()]}
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
    ) -> None:
        self.dispatcher = dispatcher
        self.endpoint = endpoint
        self.token_source = token_source or BearerTokenSource(None)
        self.allowed_origins = allowed_origins
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

    def do_GET(self) -> None:
        if self._common_checks():
            self._empty(HTTPStatus.METHOD_NOT_ALLOWED, allow="POST")

    def do_DELETE(self) -> None:
        if self._common_checks():
            self._empty(HTTPStatus.METHOD_NOT_ALLOWED, allow="POST")

    def do_POST(self) -> None:
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
) -> None:
    """Run the foreground MCP HTTP process until interrupted or terminated."""
    server = McpHttpServer(
        (host, port),
        dispatcher,
        endpoint=endpoint,
        token_source=BearerTokenSource(auth_token_file),
        allowed_origins=allowed_origins,
    )
    log.info("MCP server listening on http://%s:%d%s", host, port, endpoint)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("MCP server interrupted")
    finally:
        server.server_close()
