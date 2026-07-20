"""Shared Joplin tool definitions and Actions exposure metadata."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from joplin_md_sync.mcp_service import JoplinMcpService, ToolServiceError
from joplin_md_sync.tool_schema import mutable_json, validate_schema_definition

JsonObject = dict[str, Any]
ToolHandler = Callable[[Mapping[str, object]], JsonObject]
ActionExposure = Literal["auto", "disabled"]
ToolEffect = Literal["read", "write", "destructive", "unknown"]

_SAFE_ROUTE = re.compile(r"^[A-Za-z0-9._~-]+$")
_OPERATION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, object]
    annotations: Mapping[str, object]
    handler: ToolHandler
    action_exposure: ActionExposure
    action_exposure_reason: str | None
    output_schema: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", _freeze(self.input_schema))
        object.__setattr__(self, "annotations", _freeze(self.annotations))
        if self.output_schema is not None:
            object.__setattr__(self, "output_schema", _freeze(self.output_schema))

    def to_mcp_json(self) -> JsonObject:
        result: JsonObject = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": mutable_json(self.input_schema),
            "annotations": mutable_json(self.annotations),
        }
        if self.output_schema is not None:
            result["outputSchema"] = mutable_json(self.output_schema)
        return result


def tool_effect(tool: ToolDefinition) -> ToolEffect:
    read_only = tool.annotations.get("readOnlyHint")
    destructive = tool.annotations.get("destructiveHint")
    if read_only is True and destructive is True:
        raise ValueError(f"tool {tool.name} is both read-only and destructive")
    if read_only is True:
        return "read"
    if destructive is True:
        return "destructive"
    if read_only is False:
        return "write"
    return "unknown"


def action_route(tool: ToolDefinition) -> str:
    if _SAFE_ROUTE.fullmatch(tool.name):
        return tool.name
    return urllib.parse.quote(tool.name, safe="-._~")


def operation_id(tool: ToolDefinition) -> str:
    if not _OPERATION_ID.fullmatch(tool.name):
        raise ValueError(f"tool {tool.name!r} is not a supported Actions operationId")
    return tool.name


class ToolRegistry:
    """Immutable ordered registry and exact-name lookup."""

    def __init__(self, definitions: tuple[ToolDefinition, ...]) -> None:
        if not definitions:
            raise ValueError("tool registry must not be empty")
        by_name: dict[str, ToolDefinition] = {}
        routes: dict[str, str] = {}
        operation_ids: dict[str, str] = {}
        for tool in definitions:
            if not tool.name or tool.name in by_name:
                raise ValueError(f"duplicate or empty tool name: {tool.name!r}")
            if tool.action_exposure not in {"auto", "disabled"}:
                raise ValueError(f"tool {tool.name} is missing valid Actions exposure metadata")
            if tool.action_exposure == "disabled":
                if not tool.action_exposure_reason:
                    raise ValueError(f"disabled tool {tool.name} requires a reason")
            elif tool.action_exposure_reason is not None:
                raise ValueError(f"exposed tool {tool.name} must not have a disabled reason")
            effect = tool_effect(tool)
            if tool.action_exposure == "auto":
                if effect == "unknown":
                    raise ValueError(f"exposed tool {tool.name} has unknown effect annotations")
                validate_schema_definition(tool.input_schema, path=f"tool {tool.name}")
                route = action_route(tool)
                previous = routes.get(route)
                if previous is not None:
                    raise ValueError(f"Actions route collision: {previous} and {tool.name}")
                routes[route] = tool.name
                op_id = operation_id(tool)
                previous = operation_ids.get(op_id)
                if previous is not None:
                    raise ValueError(f"Actions operationId collision: {previous} and {tool.name}")
                operation_ids[op_id] = tool.name
            by_name[tool.name] = tool
        self._definitions = definitions
        self._by_name = MappingProxyType(by_name)
        self._exposed = tuple(
            tool for tool in definitions if tool.action_exposure == "auto"
        )
        self._by_route = MappingProxyType(
            {action_route(tool): tool for tool in self._exposed}
        )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    @property
    def exposed(self) -> tuple[ToolDefinition, ...]:
        return self._exposed

    def get(self, name: str) -> ToolDefinition | None:
        return self._by_name.get(name)

    def get_by_action_route(self, route: str) -> ToolDefinition | None:
        return self._by_route.get(route)

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

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


class _ToolRegistryBuilder:
    def __init__(self, service: JoplinMcpService) -> None:
        self._service = service

    def _build_tools(self) -> tuple[ToolDefinition, ...]:
        def exposed(
            name: str,
            title: str,
            description: str,
            input_schema: JsonObject,
            annotations: JsonObject,
            handler: ToolHandler,
        ) -> ToolDefinition:
            return ToolDefinition(
                name,
                title,
                description,
                input_schema,
                annotations,
                handler,
                action_exposure="auto",
                action_exposure_reason=None,
            )

        def disabled(
            reason: str,
            name: str,
            title: str,
            description: str,
            input_schema: JsonObject,
            annotations: JsonObject,
            handler: ToolHandler,
        ) -> ToolDefinition:
            return ToolDefinition(
                name,
                title,
                description,
                input_schema,
                annotations,
                handler,
                action_exposure="disabled",
                action_exposure_reason=reason,
            )
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
            exposed(
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
            exposed(
                "joplin_get_notebook",
                "Read a Joplin notebook",
                "Read notebook metadata, including parent, icon, timestamps, and trash state.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                read_only,
                self._get_notebook,
            ),
            exposed(
                "joplin_create_notebook",
                "Create a Joplin notebook",
                "Create a root or nested notebook with metadata.",
                _object_schema(
                    {
                        "title": {"type": "string", "minLength": 1},
                        "parent_id": {"type": "string"},
                        "icon": {
                            "type": "string",
                            "description": (
                                "JSON-serialized Joplin FolderIcon object: type 1 uses emoji, "
                                "type 2 uses dataUrl, and type 3 uses a Font Awesome name. "
                                "Use an empty string to clear the icon."
                            ),
                        },
                        "user_created_time": {"type": "integer", "minimum": 0},
                        "user_updated_time": {"type": "integer", "minimum": 0},
                    },
                    required=("title",),
                ),
                write,
                self._service.create_notebook,
            ),
            exposed(
                "joplin_update_notebook",
                "Update a Joplin notebook",
                "Rename, move, or update metadata of a notebook.",
                _object_schema(
                    {
                        "notebook_id": _NOTEBOOK_ID,
                        "title": {"type": "string", "minLength": 1},
                        "parent_id": {"type": "string"},
                        "icon": {
                            "type": "string",
                            "description": (
                                "JSON-serialized Joplin FolderIcon object: type 1 uses emoji, "
                                "type 2 uses dataUrl, and type 3 uses a Font Awesome name. "
                                "Use an empty string to clear the icon."
                            ),
                        },
                        "user_created_time": {"type": "integer", "minimum": 0},
                        "user_updated_time": {"type": "integer", "minimum": 0},
                    },
                    required=("notebook_id",),
                ),
                write,
                self._service.update_notebook,
            ),
            exposed(
                "joplin_delete_notebook",
                "Move a Joplin notebook to trash",
                "Move a notebook to Joplin trash. Permanent deletion is not exposed.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                destructive,
                self._delete_notebook,
            ),
            exposed(
                "joplin_restore_notebook",
                "Restore a Joplin notebook",
                "Restore a trashed notebook by clearing its deleted state.",
                _object_schema({"notebook_id": _NOTEBOOK_ID}, required=("notebook_id",)),
                write,
                self._restore_notebook,
            ),
            exposed(
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
            exposed(
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
            exposed(
                "joplin_get_note",
                "Read a Joplin note",
                "Read Markdown body, tags, notebook, timestamps, todo fields, and source metadata.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                read_only,
                self._get_note,
            ),
            exposed(
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
            exposed(
                "joplin_update_note",
                "Update a Joplin note",
                "Update only supplied content or metadata fields; tags replace the complete tag set.",
                _object_schema({"note_id": _NOTE_ID, **_EDITABLE_PROPERTIES}, required=("note_id",)),
                write,
                self._service.update_note,
            ),
            exposed(
                "joplin_delete_note",
                "Move a Joplin note to trash",
                "Move a note to Joplin trash. Permanent deletion is not exposed.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                destructive,
                self._delete_note,
            ),
            exposed(
                "joplin_restore_note",
                "Restore a Joplin note",
                "Restore a trashed note by clearing its deleted state.",
                _object_schema({"note_id": _NOTE_ID}, required=("note_id",)),
                write,
                self._restore_note,
            ),
            exposed(
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
            exposed(
                "joplin_list_tags",
                "List Joplin tags",
                "List tag IDs, titles, and timestamps.",
                _object_schema({"limit": _LIMIT}),
                read_only,
                self._service.list_tags,
            ),
            exposed(
                "joplin_get_tag",
                "Read a Joplin tag",
                "Read one tag and its metadata.",
                _object_schema({"tag_id": _TAG_ID}, required=("tag_id",)),
                read_only,
                self._get_tag,
            ),
            exposed(
                "joplin_create_tag",
                "Create a Joplin tag",
                "Create a tag, or return the case-insensitive title match if it exists.",
                _object_schema(
                    {"title": {"type": "string", "minLength": 1}}, required=("title",)
                ),
                write,
                self._create_tag,
            ),
            exposed(
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
            exposed(
                "joplin_delete_tag",
                "Delete a Joplin tag",
                "Permanently delete a tag and its note associations; Joplin has no tag trash.",
                _object_schema({"tag_id": _TAG_ID}, required=("tag_id",)),
                permanent_destructive,
                self._delete_tag,
            ),
            exposed(
                "joplin_list_tag_notes",
                "List notes with a Joplin tag",
                "List notes associated with one tag.",
                _object_schema(
                    {"tag_id": _TAG_ID, "limit": _LIMIT}, required=("tag_id",)
                ),
                read_only,
                self._service.list_tag_notes,
            ),
            exposed(
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
            exposed(
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
            exposed(
                "joplin_list_resources",
                "List Joplin resources",
                "List attachment metadata without downloading binary content.",
                _object_schema({"limit": _LIMIT}),
                read_only,
                self._service.list_resources,
            ),
            exposed(
                "joplin_get_resource",
                "Read Joplin resource metadata",
                "Read attachment metadata without downloading binary content.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                read_only,
                self._get_resource,
            ),
            disabled(
                "Base64 binary content can exceed the GPT Actions text and payload limits.",
                "joplin_read_resource",
                "Read Joplin resource content",
                "Read attachment metadata and up to 10 MiB of content encoded as base64.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                read_only,
                self._read_resource,
            ),
            disabled(
                "Base64 binary content can exceed the GPT Actions text and payload limits.",
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
            disabled(
                "Base64 binary content can exceed the GPT Actions text and payload limits.",
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
            exposed(
                "joplin_delete_resource",
                "Delete a Joplin resource",
                "Permanently delete an attachment; Joplin has no resource trash.",
                _object_schema({"resource_id": _RESOURCE_ID}, required=("resource_id",)),
                permanent_destructive,
                self._delete_resource,
            ),
            exposed(
                "joplin_list_note_resources",
                "List resources in a Joplin note",
                "List attachment metadata referenced by one note.",
                _object_schema(
                    {"note_id": _NOTE_ID, "limit": _LIMIT}, required=("note_id",)
                ),
                read_only,
                self._service.list_note_resources,
            ),
            exposed(
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


def build_tool_registry(service: JoplinMcpService) -> ToolRegistry:
    """Build the one registry shared by MCP discovery and GPT Actions."""
    return ToolRegistry(_ToolRegistryBuilder(service)._build_tools())
