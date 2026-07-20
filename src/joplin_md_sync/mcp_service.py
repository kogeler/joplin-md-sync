"""Joplin note operations shared by MCP and GPT Actions transports."""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from joplin_md_sync.api import JoplinClient
from joplin_md_sync.canonical import canonicalize_tags
from joplin_md_sync.errors import ApiError, AuthError

ClientFactory = Callable[[], JoplinClient]


class ToolServiceError(Exception):
    """Expected tool failure with a stable, client-visible classification."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        details: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details


_TEXT_FIELDS = frozenset(
    {
        "title",
        "body",
        "body_html",
        "base_url",
        "image_data_url",
        "crop_rect",
        "parent_id",
        "author",
        "source_url",
        "source",
        "source_application",
        "application_data",
        "user_data",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "todo_due",
        "todo_completed",
        "user_created_time",
        "user_updated_time",
        "markup_language",
    }
)
_NUMBER_FIELDS = frozenset({"latitude", "longitude", "altitude", "order"})
_BOOLEAN_FIELDS = frozenset({"is_todo"})
_EDITABLE_FIELDS = _TEXT_FIELDS | _INTEGER_FIELDS | _NUMBER_FIELDS | _BOOLEAN_FIELDS
_NOTEBOOK_EDITABLE_FIELDS = frozenset(
    {"title", "parent_id", "icon", "user_created_time", "user_updated_time"}
)
_MAX_RESOURCE_BYTES = 10 * 1024 * 1024


class JoplinMcpService:
    """Validated Joplin entity operations with bounded availability waits."""

    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        availability_timeout: float = 10.0,
        retry_delay: float = 1.0,
    ) -> None:
        if availability_timeout < 0:
            raise ValueError("availability_timeout must be non-negative")
        if retry_delay <= 0:
            raise ValueError("retry_delay must be positive")
        self._client_factory = client_factory
        self._availability_timeout = availability_timeout
        self._retry_delay = retry_delay

    def _client_when_available(self) -> JoplinClient:
        deadline = time.monotonic() + self._availability_timeout
        last_error: ApiError | None = None
        while True:
            try:
                client = self._client_factory()
                if client.ping():
                    return client
                last_error = ApiError("Joplin API returned an unexpected ping response")
            except AuthError:
                raise
            except ApiError as exc:
                last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                assert last_error is not None
                raise last_error
            time.sleep(min(self._retry_delay, remaining))

    @staticmethod
    def _item_id(value: object, *, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ToolServiceError(f"{name} must be a non-empty string", code="INVALID_ARGUMENT")
        return value.strip()

    @classmethod
    def _note_id(cls, value: object) -> str:
        return cls._item_id(value, name="note_id")

    @classmethod
    def _notebook_id(cls, value: object) -> str:
        return cls._item_id(value, name="notebook_id")

    @classmethod
    def _tag_id(cls, value: object) -> str:
        return cls._item_id(value, name="tag_id")

    @classmethod
    def _resource_id(cls, value: object) -> str:
        return cls._item_id(value, name="resource_id")

    @staticmethod
    def _nonempty_text(value: object, *, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ToolServiceError(f"{name} must be a non-empty string", code="INVALID_ARGUMENT")
        return value.strip()

    @staticmethod
    def _folder_icon(value: object) -> str:
        if not isinstance(value, str):
            raise ToolServiceError("icon must be a string", code="INVALID_ARGUMENT")
        if not value:
            return ""
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ToolServiceError(
                "icon must be a JSON-serialized Joplin FolderIcon object",
                code="INVALID_ARGUMENT",
            ) from None
        if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
            raise ToolServiceError(
                "icon must encode one JSON object", code="INVALID_ARGUMENT"
            )
        allowed = {"type", "emoji", "name", "dataUrl"}
        unknown = set(parsed) - allowed
        if unknown:
            raise ToolServiceError(
                f"icon contains unsupported field(s): {', '.join(sorted(unknown))}",
                code="INVALID_ARGUMENT",
            )
        icon_type = parsed.get("type")
        if isinstance(icon_type, bool) or not isinstance(icon_type, int) or icon_type not in {1, 2, 3}:
            raise ToolServiceError(
                "icon.type must be 1 (emoji), 2 (data URL), or 3 (Font Awesome)",
                code="INVALID_ARGUMENT",
            )
        normalized: dict[str, object] = {"type": icon_type}
        for field in ("emoji", "name", "dataUrl"):
            field_value = parsed.get(field, "")
            if not isinstance(field_value, str):
                raise ToolServiceError(
                    f"icon.{field} must be a string", code="INVALID_ARGUMENT"
                )
            normalized[field] = field_value
        required_field = {1: "emoji", 2: "dataUrl", 3: "name"}[icon_type]
        if not str(normalized[required_field]).strip():
            raise ToolServiceError(
                f"icon.{required_field} must be non-empty for icon.type {icon_type}",
                code="INVALID_ARGUMENT",
            )
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _limit(value: object, *, default: int = 100) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ToolServiceError(
                "limit must be an integer between 1 and 100", code="INVALID_ARGUMENT"
            )
        return value

    @staticmethod
    def _boolean(value: object, *, name: str, default: bool = False) -> bool:
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ToolServiceError(f"{name} must be a boolean", code="INVALID_ARGUMENT")
        return value

    @staticmethod
    def _tags(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ToolServiceError("tags must be an array of strings", code="INVALID_ARGUMENT")
        return canonicalize_tags(value)

    @staticmethod
    def _editable_fields(arguments: Mapping[str, object]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        unknown = set(arguments) - _EDITABLE_FIELDS - {"note_id", "tags"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}",
                code="INVALID_ARGUMENT",
            )
        for key in _TEXT_FIELDS:
            if key in arguments:
                value = arguments[key]
                if not isinstance(value, str):
                    raise ToolServiceError(f"{key} must be a string", code="INVALID_ARGUMENT")
                fields[key] = value
        for key in _INTEGER_FIELDS:
            if key in arguments:
                value = arguments[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ToolServiceError(
                        f"{key} must be a non-negative integer", code="INVALID_ARGUMENT"
                    )
                fields[key] = value
        for key in _NUMBER_FIELDS:
            if key in arguments:
                value = arguments[key]
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise ToolServiceError(f"{key} must be a number", code="INVALID_ARGUMENT")
                fields[key] = value
        for key in _BOOLEAN_FIELDS:
            if key in arguments:
                value = arguments[key]
                if not isinstance(value, bool):
                    raise ToolServiceError(f"{key} must be a boolean", code="INVALID_ARGUMENT")
                fields[key] = int(value)
        return fields

    @staticmethod
    def _require_note(client: JoplinClient, note_id: str) -> dict[str, Any]:
        note = client.get_note(
            note_id, include_deleted=True, fields=JoplinClient.NOTE_METADATA_FIELDS
        )
        if note is None:
            raise ToolServiceError(
                f"note not found: {note_id}", code="NOTE_NOT_FOUND", details={"note_id": note_id}
            )
        return note

    @staticmethod
    def _require_notebook(client: JoplinClient, notebook_id: str) -> dict[str, Any]:
        notebook = client.get_folder(notebook_id, include_deleted=True)
        if notebook is None:
            raise ToolServiceError(
                f"notebook not found: {notebook_id}",
                code="NOTEBOOK_NOT_FOUND",
                details={"notebook_id": notebook_id},
            )
        return notebook

    @staticmethod
    def _require_tag(client: JoplinClient, tag_id: str) -> dict[str, Any]:
        tag = client.get_tag(tag_id)
        if tag is None:
            raise ToolServiceError(
                f"tag not found: {tag_id}",
                code="TAG_NOT_FOUND",
                details={"tag_id": tag_id},
            )
        return tag

    @staticmethod
    def _require_resource(client: JoplinClient, resource_id: str) -> dict[str, Any]:
        resource = client.get_resource(resource_id)
        if resource is None:
            raise ToolServiceError(
                f"resource not found: {resource_id}",
                code="RESOURCE_NOT_FOUND",
                details={"resource_id": resource_id},
            )
        return resource

    @staticmethod
    def _resource_data(value: object) -> bytes:
        if not isinstance(value, str) or not value:
            raise ToolServiceError(
                "content_base64 must be a non-empty base64 string", code="INVALID_ARGUMENT"
            )
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise ToolServiceError(
                "content_base64 is not valid base64", code="INVALID_ARGUMENT"
            ) from None
        if len(data) > _MAX_RESOURCE_BYTES:
            raise ToolServiceError(
                f"decoded resource exceeds {_MAX_RESOURCE_BYTES} bytes",
                code="RESOURCE_TOO_LARGE",
                details={"max_bytes": _MAX_RESOURCE_BYTES, "actual_bytes": len(data)},
            )
        return data

    @classmethod
    def _attachment_specs(cls, value: object) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ToolServiceError("attachments must be an array", code="INVALID_ARGUMENT")
        attachments: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict) or any(not isinstance(key, str) for key in item):
                raise ToolServiceError(
                    f"attachments[{index}] must be an object", code="INVALID_ARGUMENT"
                )
            unknown = set(item) - {"filename", "mime", "title", "alt_text", "content_base64"}
            if unknown:
                raise ToolServiceError(
                    f"attachments[{index}] unsupported field(s): {', '.join(sorted(unknown))}",
                    code="INVALID_ARGUMENT",
                )
            filename = cls._nonempty_text(item.get("filename"), name=f"attachments[{index}].filename")
            mime = cls._nonempty_text(item.get("mime"), name=f"attachments[{index}].mime")
            if any(character in filename for character in "\r\n") or any(
                character in mime for character in "\r\n"
            ):
                raise ToolServiceError(
                    f"attachments[{index}] filename and mime cannot contain newlines",
                    code="INVALID_ARGUMENT",
                )
            title_value = item.get("title")
            alt_value = item.get("alt_text")
            if title_value is not None and not isinstance(title_value, str):
                raise ToolServiceError(
                    f"attachments[{index}].title must be a string", code="INVALID_ARGUMENT"
                )
            if alt_value is not None and not isinstance(alt_value, str):
                raise ToolServiceError(
                    f"attachments[{index}].alt_text must be a string", code="INVALID_ARGUMENT"
                )
            attachments.append(
                {
                    "filename": filename,
                    "mime": mime,
                    "title": title_value.strip() if isinstance(title_value, str) else filename,
                    "alt_text": alt_value if isinstance(alt_value, str) else filename,
                    "data": cls._resource_data(item.get("content_base64")),
                }
            )
        return attachments

    @staticmethod
    def _enrich_note(client: JoplinClient, note: Mapping[str, Any]) -> dict[str, Any]:
        note_id = str(note.get("id") or "")
        parent_id = str(note.get("parent_id") or "")
        tags = sorted(
            str(tag.get("title") or "")
            for tag in client.list_note_tags(note_id)
            if tag.get("title")
        )
        notebook = client.get_folder(parent_id) if parent_id else None
        metadata = {
            key: value for key, value in note.items() if key not in {"id", "title", "body"}
        }
        metadata["tags"] = tags
        metadata["notebook"] = notebook
        metadata["resources"] = client.list_note_resources(note_id)
        return {
            "id": note_id,
            "title": str(note.get("title") or ""),
            "body": str(note.get("body") or ""),
            "metadata": metadata,
        }

    @staticmethod
    def _set_tags(client: JoplinClient, note_id: str, requested: tuple[str, ...]) -> None:
        existing = {
            str(tag.get("title") or "").strip().lower(): str(tag.get("id") or "")
            for tag in client.list_note_tags(note_id)
            if tag.get("title") and tag.get("id")
        }
        all_tags = {
            str(tag.get("title") or "").strip().lower(): str(tag.get("id") or "")
            for tag in client.list_tags()
            if tag.get("title") and tag.get("id")
        }
        target = set(requested)
        for title in sorted(target - set(existing)):
            tag_id = all_tags.get(title)
            if not tag_id:
                created = client.create_tag(title)
                tag_id = str(created.get("id") or "")
            if not tag_id:
                raise ToolServiceError(
                    f"Joplin did not return an id for tag {title!r}", code="INVALID_API_RESPONSE"
                )
            client.add_tag_to_note(tag_id, note_id)
        for title in sorted(set(existing) - target):
            client.remove_tag_from_note(existing[title], note_id)

    def list_notebooks(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"limit", "include_deleted"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        limit = self._limit(arguments.get("limit"))
        include_deleted = self._boolean(
            arguments.get("include_deleted"), name="include_deleted"
        )
        client = self._client_when_available()
        notebooks = client.list_folders(
            include_deleted=include_deleted, max_results=limit
        )
        return {"notebooks": notebooks, "count": len(notebooks), "limit": limit}

    def get_notebook(self, notebook_id_value: object) -> dict[str, Any]:
        notebook_id = self._notebook_id(notebook_id_value)
        client = self._client_when_available()
        return {"notebook": self._require_notebook(client, notebook_id)}

    def create_notebook(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - _NOTEBOOK_EDITABLE_FIELDS
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        title = self._nonempty_text(arguments.get("title"), name="title")
        parent_id_value = arguments.get("parent_id", "")
        if not isinstance(parent_id_value, str):
            raise ToolServiceError("parent_id must be a string", code="INVALID_ARGUMENT")
        parent_id = parent_id_value.strip()
        fields: dict[str, Any] = {}
        if "icon" in arguments:
            fields["icon"] = self._folder_icon(arguments["icon"])
        for name in ("user_created_time", "user_updated_time"):
            if name in arguments:
                value = arguments[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ToolServiceError(
                        f"{name} must be a non-negative integer", code="INVALID_ARGUMENT"
                    )
                fields[name] = value
        client = self._client_when_available()
        if parent_id:
            self._require_notebook(client, parent_id)
        created = client.create_folder(title=title, parent_id=parent_id)
        notebook_id = str(created.get("id") or "")
        if not notebook_id:
            raise ToolServiceError(
                "Joplin did not return an id for the new notebook",
                code="INVALID_API_RESPONSE",
            )
        if fields:
            client.update_folder(notebook_id, fields)
        return {"notebook": self._require_notebook(client, notebook_id)}

    def update_notebook(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - _NOTEBOOK_EDITABLE_FIELDS - {"notebook_id"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        notebook_id = self._notebook_id(arguments.get("notebook_id"))
        fields: dict[str, Any] = {}
        for name in ("title", "parent_id"):
            if name in arguments:
                value = arguments[name]
                if not isinstance(value, str):
                    raise ToolServiceError(f"{name} must be a string", code="INVALID_ARGUMENT")
                if name == "title" and not value.strip():
                    raise ToolServiceError("title must be a non-empty string", code="INVALID_ARGUMENT")
                fields[name] = value.strip() if name in {"title", "parent_id"} else value
        if "icon" in arguments:
            fields["icon"] = self._folder_icon(arguments["icon"])
        for name in ("user_created_time", "user_updated_time"):
            if name in arguments:
                value = arguments[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ToolServiceError(
                        f"{name} must be a non-negative integer", code="INVALID_ARGUMENT"
                    )
                fields[name] = value
        if not fields:
            raise ToolServiceError(
                "at least one notebook field must be provided", code="INVALID_ARGUMENT"
            )
        client = self._client_when_available()
        self._require_notebook(client, notebook_id)
        parent_id = fields.get("parent_id")
        if isinstance(parent_id, str) and parent_id:
            if parent_id == notebook_id:
                raise ToolServiceError(
                    "a notebook cannot be its own parent", code="INVALID_ARGUMENT"
                )
            self._require_notebook(client, parent_id)
        client.update_folder(notebook_id, fields)
        return {"notebook": self._require_notebook(client, notebook_id)}

    def delete_notebook(self, notebook_id_value: object) -> dict[str, Any]:
        notebook_id = self._notebook_id(notebook_id_value)
        client = self._client_when_available()
        notebook = self._require_notebook(client, notebook_id)
        if notebook.get("deleted_time"):
            return {"notebook_id": notebook_id, "trashed": True, "already_trashed": True}
        client.delete_folder(notebook_id)
        return {"notebook_id": notebook_id, "trashed": True, "already_trashed": False}

    def restore_notebook(self, notebook_id_value: object) -> dict[str, Any]:
        notebook_id = self._notebook_id(notebook_id_value)
        client = self._client_when_available()
        notebook = self._require_notebook(client, notebook_id)
        if not notebook.get("deleted_time"):
            return {"notebook": notebook, "restored": True, "already_active": True}
        client.restore_folder(notebook_id)
        return {
            "notebook": self._require_notebook(client, notebook_id),
            "restored": True,
            "already_active": False,
        }

    def list_notebook_notes(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {
            "notebook_id",
            "limit",
            "include_deleted",
            "include_conflicts",
        }
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        notebook_id = self._notebook_id(arguments.get("notebook_id"))
        limit = self._limit(arguments.get("limit"))
        include_deleted = self._boolean(
            arguments.get("include_deleted"), name="include_deleted"
        )
        include_conflicts = self._boolean(
            arguments.get("include_conflicts"), name="include_conflicts"
        )
        client = self._client_when_available()
        self._require_notebook(client, notebook_id)
        notes = client.list_folder_notes(
            notebook_id,
            include_deleted=include_deleted,
            include_conflicts=include_conflicts,
            max_results=limit,
        )
        return {"notebook_id": notebook_id, "notes": notes, "count": len(notes), "limit": limit}

    def list_notes(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        limit = self._limit(arguments.get("limit"))
        include_deleted = self._boolean(
            arguments.get("include_deleted"), name="include_deleted"
        )
        include_conflicts = self._boolean(
            arguments.get("include_conflicts"), name="include_conflicts"
        )
        unknown = set(arguments) - {"limit", "include_deleted", "include_conflicts"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        client = self._client_when_available()
        notes = client.list_notes(
            include_deleted=include_deleted,
            include_conflicts=include_conflicts,
            max_results=limit,
        )
        return {"notes": notes, "count": len(notes), "limit": limit}

    def get_note(self, note_id_value: object) -> dict[str, Any]:
        note_id = self._note_id(note_id_value)
        client = self._client_when_available()
        return {"note": self._enrich_note(client, self._require_note(client, note_id))}

    def create_note(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - _EDITABLE_FIELDS - {"tags", "notebook_title", "attachments"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        editable_arguments = {
            key: value
            for key, value in arguments.items()
            if key not in {"notebook_title", "attachments"}
        }
        fields = self._editable_fields(editable_arguments)
        title = fields.pop("title", None)
        if not isinstance(title, str) or not title.strip():
            raise ToolServiceError("title must be a non-empty string", code="INVALID_ARGUMENT")
        has_body = "body" in fields
        body = fields.pop("body", None)
        if has_body and "body_html" in fields:
            raise ToolServiceError(
                "body and body_html are mutually exclusive", code="INVALID_ARGUMENT"
            )
        if body is None and "body_html" not in fields:
            body = ""
        parent_id = fields.pop("parent_id", "")
        tags = self._tags(arguments.get("tags"))
        attachments = self._attachment_specs(arguments.get("attachments"))
        notebook_title = arguments.get("notebook_title")
        if notebook_title is not None and (
            not isinstance(notebook_title, str) or not notebook_title.strip()
        ):
            raise ToolServiceError(
                "notebook_title must be a non-empty string", code="INVALID_ARGUMENT"
            )
        if parent_id and notebook_title is not None:
            raise ToolServiceError(
                "parent_id and notebook_title are mutually exclusive", code="INVALID_ARGUMENT"
            )
        client = self._client_when_available()
        if parent_id:
            if client.get_folder(parent_id) is None:
                raise ToolServiceError(
                    f"notebook not found: {parent_id}",
                    code="NOTEBOOK_NOT_FOUND",
                    details={"parent_id": parent_id},
                )
        else:
            requested_title = (
                notebook_title.strip() if isinstance(notebook_title, str) else "MCP Notes"
            )
            matching = next(
                (
                    folder
                    for folder in client.list_folders()
                    if folder.get("title") == requested_title
                ),
                None,
            )
            folder = matching or client.create_folder(title=requested_title)
            parent_id = str(folder.get("id") or "")
            if not parent_id:
                raise ToolServiceError(
                    "Joplin did not return an id for the note notebook",
                    code="INVALID_API_RESPONSE",
                )
        created = client.create_note(
            title=title, body=body, parent_id=parent_id, extra_fields=fields
        )
        note_id = str(created.get("id") or "")
        if not note_id:
            raise ToolServiceError(
                "Joplin did not return an id for the new note", code="INVALID_API_RESPONSE"
            )
        if tags:
            self._set_tags(client, note_id, tags)
        attached_resources: list[dict[str, Any]] = []
        if attachments:
            try:
                links: list[str] = []
                for attachment in attachments:
                    uploaded = client.create_resource(
                        attachment["data"],
                        filename=attachment["filename"],
                        mime=attachment["mime"],
                        title=attachment["title"],
                    )
                    resource_id = str(uploaded.get("id") or "")
                    if not resource_id:
                        raise ToolServiceError(
                            "Joplin did not return an id for an uploaded attachment",
                            code="INVALID_API_RESPONSE",
                        )
                    resource = self._require_resource(client, resource_id)
                    attached_resources.append(resource)
                    label = str(attachment["alt_text"])
                    label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                    prefix = "!" if str(attachment["mime"]).casefold().startswith("image/") else ""
                    links.append(f"{prefix}[{label}](:/{resource_id})")
                current = self._require_note(client, note_id)
                current_body = str(current.get("body") or "")
                separator = "\n\n" if current_body else ""
                client.update_note(note_id, {"body": current_body + separator + "\n\n".join(links)})
            except (ApiError, ToolServiceError) as exc:
                raise ToolServiceError(
                    f"note was created but attachment processing did not complete: {exc}",
                    code="PARTIAL_NOTE_CREATE",
                    details={
                        "note_id": note_id,
                        "created_resource_ids": [
                            str(resource.get("id") or "") for resource in attached_resources
                        ],
                    },
                ) from None
        result: dict[str, Any] = {
            "note": self._enrich_note(client, self._require_note(client, note_id))
        }
        if attached_resources:
            result["created_resources"] = attached_resources
        return result

    def update_note(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        note_id = self._note_id(arguments.get("note_id"))
        fields = self._editable_fields(arguments)
        if "body" in fields and "body_html" in fields:
            raise ToolServiceError(
                "body and body_html are mutually exclusive", code="INVALID_ARGUMENT"
            )
        has_tags = "tags" in arguments
        tags = self._tags(arguments.get("tags")) if has_tags else ()
        if not fields and not has_tags:
            raise ToolServiceError(
                "at least one note field or tags must be provided", code="INVALID_ARGUMENT"
            )
        client = self._client_when_available()
        self._require_note(client, note_id)
        if fields:
            client.update_note(note_id, fields)
        if has_tags:
            self._set_tags(client, note_id, tags)
        return {"note": self._enrich_note(client, self._require_note(client, note_id))}

    def delete_note(self, note_id_value: object) -> dict[str, Any]:
        note_id = self._note_id(note_id_value)
        client = self._client_when_available()
        note = self._require_note(client, note_id)
        if note.get("deleted_time"):
            return {"note_id": note_id, "trashed": True, "already_trashed": True}
        client.delete_note(note_id)
        return {"note_id": note_id, "trashed": True, "already_trashed": False}

    def restore_note(self, note_id_value: object) -> dict[str, Any]:
        note_id = self._note_id(note_id_value)
        client = self._client_when_available()
        note = self._require_note(client, note_id)
        if not note.get("deleted_time"):
            return {
                "note": self._enrich_note(client, note),
                "restored": True,
                "already_active": True,
            }
        client.restore_note(note_id)
        return {
            "note": self._enrich_note(client, self._require_note(client, note_id)),
            "restored": True,
            "already_active": False,
        }

    def search_notes(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"query", "limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolServiceError("query must be a non-empty string", code="INVALID_ARGUMENT")
        limit = self._limit(arguments.get("limit"), default=50)
        client = self._client_when_available()
        notes = client.search_notes(query.strip(), max_results=limit)
        return {"query": query.strip(), "notes": notes, "count": len(notes), "limit": limit}

    def list_tags(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        limit = self._limit(arguments.get("limit"))
        client = self._client_when_available()
        tags = client.list_tags(max_results=limit)
        return {"tags": tags, "count": len(tags), "limit": limit}

    def get_tag(self, tag_id_value: object) -> dict[str, Any]:
        tag_id = self._tag_id(tag_id_value)
        client = self._client_when_available()
        return {"tag": self._require_tag(client, tag_id)}

    def create_tag(self, title_value: object) -> dict[str, Any]:
        title = self._nonempty_text(title_value, name="title")
        client = self._client_when_available()
        existing = next(
            (
                tag
                for tag in client.list_tags()
                if str(tag.get("title") or "").strip().casefold() == title.casefold()
            ),
            None,
        )
        if existing is not None:
            return {"tag": existing, "created": False}
        created = client.create_tag(title)
        tag_id = str(created.get("id") or "")
        if not tag_id:
            raise ToolServiceError(
                "Joplin did not return an id for the new tag", code="INVALID_API_RESPONSE"
            )
        return {"tag": self._require_tag(client, tag_id), "created": True}

    def update_tag(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"tag_id", "title"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        tag_id = self._tag_id(arguments.get("tag_id"))
        title = self._nonempty_text(arguments.get("title"), name="title")
        client = self._client_when_available()
        self._require_tag(client, tag_id)
        client.update_tag(tag_id, {"title": title})
        return {"tag": self._require_tag(client, tag_id)}

    def delete_tag(self, tag_id_value: object) -> dict[str, Any]:
        tag_id = self._tag_id(tag_id_value)
        client = self._client_when_available()
        self._require_tag(client, tag_id)
        client.delete_tag(tag_id)
        return {"tag_id": tag_id, "deleted": True, "permanent": True}

    def list_tag_notes(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"tag_id", "limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        tag_id = self._tag_id(arguments.get("tag_id"))
        limit = self._limit(arguments.get("limit"))
        client = self._client_when_available()
        self._require_tag(client, tag_id)
        notes = client.list_tag_notes(tag_id, max_results=limit)
        return {"tag_id": tag_id, "notes": notes, "count": len(notes), "limit": limit}

    def add_tag_to_note(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"tag_id", "note_id"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        tag_id = self._tag_id(arguments.get("tag_id"))
        note_id = self._note_id(arguments.get("note_id"))
        client = self._client_when_available()
        self._require_tag(client, tag_id)
        self._require_note(client, note_id)
        current_ids = {str(tag.get("id") or "") for tag in client.list_note_tags(note_id)}
        already_attached = tag_id in current_ids
        if not already_attached:
            client.add_tag_to_note(tag_id, note_id)
        return {
            "note": self._enrich_note(client, self._require_note(client, note_id)),
            "tag_id": tag_id,
            "attached": True,
            "already_attached": already_attached,
        }

    def remove_tag_from_note(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"tag_id", "note_id"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        tag_id = self._tag_id(arguments.get("tag_id"))
        note_id = self._note_id(arguments.get("note_id"))
        client = self._client_when_available()
        self._require_tag(client, tag_id)
        self._require_note(client, note_id)
        current_ids = {str(tag.get("id") or "") for tag in client.list_note_tags(note_id)}
        was_attached = tag_id in current_ids
        if was_attached:
            client.remove_tag_from_note(tag_id, note_id)
        return {
            "note": self._enrich_note(client, self._require_note(client, note_id)),
            "tag_id": tag_id,
            "removed": True,
            "was_attached": was_attached,
        }

    def list_resources(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        limit = self._limit(arguments.get("limit"))
        client = self._client_when_available()
        resources = client.list_resources(max_results=limit)
        return {"resources": resources, "count": len(resources), "limit": limit}

    def get_resource(self, resource_id_value: object) -> dict[str, Any]:
        resource_id = self._resource_id(resource_id_value)
        client = self._client_when_available()
        return {"resource": self._require_resource(client, resource_id)}

    def read_resource(self, resource_id_value: object) -> dict[str, Any]:
        resource_id = self._resource_id(resource_id_value)
        client = self._client_when_available()
        resource = self._require_resource(client, resource_id)
        data = client.get_resource_file(resource_id)
        if len(data) > _MAX_RESOURCE_BYTES:
            raise ToolServiceError(
                f"resource exceeds {_MAX_RESOURCE_BYTES} bytes",
                code="RESOURCE_TOO_LARGE",
                details={"max_bytes": _MAX_RESOURCE_BYTES, "actual_bytes": len(data)},
            )
        return {
            "resource": resource,
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    def create_resource(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"filename", "mime", "title", "content_base64"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        filename = self._nonempty_text(arguments.get("filename"), name="filename")
        mime = self._nonempty_text(arguments.get("mime"), name="mime")
        title_value = arguments.get("title")
        if title_value is not None and not isinstance(title_value, str):
            raise ToolServiceError("title must be a string", code="INVALID_ARGUMENT")
        data = self._resource_data(arguments.get("content_base64"))
        client = self._client_when_available()
        created = client.create_resource(
            data,
            filename=filename,
            mime=mime,
            title=title_value if isinstance(title_value, str) else None,
        )
        resource_id = str(created.get("id") or "")
        if not resource_id:
            raise ToolServiceError(
                "Joplin did not return an id for the new resource", code="INVALID_API_RESPONSE"
            )
        return {"resource": self._require_resource(client, resource_id)}

    def update_resource(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {
            "resource_id",
            "filename",
            "mime",
            "title",
            "content_base64",
        }
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        resource_id = self._resource_id(arguments.get("resource_id"))
        has_content = "content_base64" in arguments
        fields: dict[str, Any] = {}
        for name in ("title", "filename", "mime"):
            if name in arguments:
                value = arguments[name]
                if not isinstance(value, str) or not value.strip():
                    raise ToolServiceError(
                        f"{name} must be a non-empty string", code="INVALID_ARGUMENT"
                    )
                fields[name] = value.strip()
        if not fields and not has_content:
            raise ToolServiceError(
                "at least one resource field or content_base64 must be provided",
                code="INVALID_ARGUMENT",
            )
        client = self._client_when_available()
        current = self._require_resource(client, resource_id)
        data = self._resource_data(arguments.get("content_base64")) if has_content else None
        filename = str(fields.get("filename") or current.get("filename") or current.get("title") or resource_id)
        mime = str(fields.get("mime") or current.get("mime") or "application/octet-stream")
        client.update_resource(
            resource_id,
            fields,
            data=data,
            filename=filename if data is not None else None,
            mime=mime if data is not None else None,
        )
        return {"resource": self._require_resource(client, resource_id)}

    def delete_resource(self, resource_id_value: object) -> dict[str, Any]:
        resource_id = self._resource_id(resource_id_value)
        client = self._client_when_available()
        self._require_resource(client, resource_id)
        client.delete_resource(resource_id)
        return {"resource_id": resource_id, "deleted": True, "permanent": True}

    def list_note_resources(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"note_id", "limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        note_id = self._note_id(arguments.get("note_id"))
        limit = self._limit(arguments.get("limit"))
        client = self._client_when_available()
        self._require_note(client, note_id)
        resources = client.list_note_resources(note_id, max_results=limit)
        return {"note_id": note_id, "resources": resources, "count": len(resources), "limit": limit}

    def list_resource_notes(self, arguments: Mapping[str, object]) -> dict[str, Any]:
        unknown = set(arguments) - {"resource_id", "limit"}
        if unknown:
            raise ToolServiceError(
                f"unsupported argument(s): {', '.join(sorted(unknown))}", code="INVALID_ARGUMENT"
            )
        resource_id = self._resource_id(arguments.get("resource_id"))
        limit = self._limit(arguments.get("limit"))
        client = self._client_when_available()
        self._require_resource(client, resource_id)
        notes = client.list_resource_notes(resource_id, max_results=limit)
        return {"resource_id": resource_id, "notes": notes, "count": len(notes), "limit": limit}
