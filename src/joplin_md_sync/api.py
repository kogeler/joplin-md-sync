"""Joplin Data API client (stdlib urllib only).

Safety properties:
* the token never appears in logs, exceptions, or reprs — every outgoing
  message passes through :func:`redact`;
* idempotent GETs are retried with bounded exponential backoff;
* writes are never blindly retried — ambiguous failures surface as
  :class:`AmbiguousWriteError` so the caller re-reads and decides;
* non-loopback endpoints are rejected unless explicitly allowed.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from joplin_md_sync.errors import ApiError, AuthError

log = logging.getLogger("joplin_md_sync.api")

DEFAULT_PORT = 41184
DISCOVERY_PORTS = range(41184, 41195)
PAGE_LIMIT = 100
PING_RESPONSE = "JoplinClipperServer"

_GET_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5


class AmbiguousWriteError(ApiError):
    """A write timed out or failed in a way where it may have been applied."""


def redact(text: str, token: str) -> str:
    """Remove the token from any outgoing message."""
    if token:
        text = text.replace(token, "***")
    return text


def is_loopback_url(base_url: str) -> bool:
    host = urllib.parse.urlsplit(base_url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class JoplinClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        retries: int = _GET_RETRIES,
        backoff_base: float = _BACKOFF_BASE_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._retries = retries
        self._backoff_base = backoff_base

    def __repr__(self) -> str:  # never leak the token
        return f"JoplinClient(base_url={self.base_url!r}, token='***')"

    # --- low-level -----------------------------------------------------

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query = dict(params or {})
        query["token"] = self._token
        return f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        raw: bool = False,
    ) -> Any:
        url = self._url(path, params)
        if payload is not None and body is not None:
            raise ValueError("payload and body are mutually exclusive")
        data = json.dumps(payload).encode("utf-8") if payload is not None else body
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type or "application/json")

        idempotent = method == "GET"
        attempts = self._retries if idempotent else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            if attempt:
                delay = self._backoff_base * (2 ** (attempt - 1))
                log.debug("retrying %s %s in %.1fs (attempt %d)", method, path, delay, attempt + 1)
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                if raw:
                    return body
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = redact(exc.read().decode("utf-8", "replace")[:500], self._token)
                if exc.code in (401, 403):
                    raise AuthError(
                        f"Joplin API rejected the request ({exc.code}) for {method} {path}: {detail}",
                        status=exc.code,
                    ) from None
                if exc.code == 404:
                    raise ApiError(
                        f"not found: {method} {path}", status=404, code="API_NOT_FOUND"
                    ) from None
                # Server-side errors on GET are retryable; on writes they are not.
                last_exc = ApiError(
                    f"Joplin API error {exc.code} for {method} {path}: {detail}", status=exc.code
                )
                if not idempotent:
                    raise last_exc from None
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
                reason = redact(str(exc), self._token)
                if not idempotent:
                    # The write may or may not have been applied.
                    raise AmbiguousWriteError(
                        f"ambiguous failure during {method} {path}: {reason}"
                    ) from None
                last_exc = ApiError(f"Joplin API unreachable for {method} {path}: {reason}")
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _multipart_resource_body(
        data: bytes, *, filename: str, mime: str, props: dict[str, Any]
    ) -> tuple[bytes, str]:
        boundary = f"----joplin-md-sync-{secrets.token_hex(16)}"
        safe_filename = filename.replace("\\", "_").replace('"', "'")
        safe_filename = safe_filename.replace("\r", "_").replace("\n", "_")
        safe_mime = mime.replace("\r", "").replace("\n", "")
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; filename="{safe_filename}"\r\n'
            f"Content-Type: {safe_mime}\r\n\r\n"
        ).encode()
        props_part = (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="props"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode("ascii")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        body = prefix + data + props_part + json.dumps(props).encode("utf-8") + suffix
        return body, f"multipart/form-data; boundary={boundary}"

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_results: int | None = None,
        order_by: str | None = "id",
        order_dir: str = "ASC",
    ) -> Iterator[dict[str, Any]]:
        page = 1
        emitted = 0
        while True:
            merged = dict(params or {})
            page_limit = PAGE_LIMIT
            if max_results is not None:
                page_limit = min(page_limit, max_results - emitted)
                if page_limit <= 0:
                    return
            merged.update({"limit": page_limit, "page": page})
            if order_by is not None:
                merged.update({"order_by": order_by, "order_dir": order_dir})
            data = self._request("GET", path, params=merged)
            items = data.get("items", []) if isinstance(data, dict) else []
            for item in items:
                if isinstance(item, dict):
                    yield item
                    emitted += 1
                    if max_results is not None and emitted >= max_results:
                        return
            if not (isinstance(data, dict) and data.get("has_more")):
                return
            page += 1

    # --- service --------------------------------------------------------

    def ping(self) -> bool:
        body = self._request("GET", "/ping", raw=True)
        return body.decode("utf-8", "replace").strip() == PING_RESPONSE

    # --- notes ----------------------------------------------------------

    _NOTE_LIST_FIELDS = "id,parent_id,title,updated_time"
    _NOTE_FIELDS = "id,parent_id,title,body,updated_time,is_conflict,deleted_time"
    NOTE_METADATA_FIELDS = (
        "id,parent_id,title,body,created_time,updated_time,is_conflict,deleted_time,"
        "author,source_url,is_todo,todo_due,todo_completed,user_created_time,"
        "user_updated_time,markup_language,latitude,longitude,altitude,order,source,"
        "source_application,application_data,user_data"
    )

    def list_notes(
        self,
        *,
        include_deleted: bool = False,
        include_conflicts: bool = False,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": fields or self._NOTE_LIST_FIELDS + ",is_conflict,deleted_time"
        }
        if include_deleted:
            params["include_deleted"] = 1
        if include_conflicts:
            params["include_conflicts"] = 1
        return list(self._paginate("/notes", params=params, max_results=max_results))

    def get_note(
        self,
        note_id: str,
        *,
        include_deleted: bool = False,
        fields: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one full note; None when it does not exist (404)."""
        params: dict[str, Any] = {"fields": fields or self._NOTE_FIELDS}
        if include_deleted:
            params["include_deleted"] = 1
        try:
            return self._request("GET", f"/notes/{note_id}", params=params)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def create_note(
        self,
        *,
        title: str,
        body: str | None,
        parent_id: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(extra_fields or {})
        payload.update({"title": title, "parent_id": parent_id})
        if body is not None:
            payload["body"] = body
        return self._request(
            "POST", "/notes", payload=payload
        )

    def create_note_with_id(
        self, *, note_id: str, title: str, body: str, parent_id: str
    ) -> dict[str, Any]:
        """Recreate a note under a known id (conflict resolution of deletions)."""
        return self._request(
            "POST", "/notes",
            payload={"id": note_id, "title": title, "body": body, "parent_id": parent_id},
        )

    def update_note(self, note_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PUT only the supplied fields (Joplin merges partial updates)."""
        return self._request("PUT", f"/notes/{note_id}", payload=fields)

    def delete_note(self, note_id: str, *, permanent: bool = False) -> None:
        # permanent deletion is intentionally never used by the sync engine.
        params = {"permanent": 1} if permanent else None
        self._request("DELETE", f"/notes/{note_id}", params=params)

    def restore_note(self, note_id: str) -> dict[str, Any]:
        """Restore a trashed note by clearing ``deleted_time``.

        Verified against real Joplin: ``PUT {"deleted_time": 0}`` restores a
        note from the trash, while ``POST /notes`` with an existing id fails
        with a UNIQUE-constraint error.
        """
        return self._request("PUT", f"/notes/{note_id}", payload={"deleted_time": 0})

    def search_notes(
        self,
        query: str,
        *,
        fields: str | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Run a Joplin full-text query, preserving relevance ordering."""
        params: dict[str, Any] = {
            "query": query,
            "fields": fields or self._NOTE_LIST_FIELDS + ",is_conflict,deleted_time",
        }
        return list(
            self._paginate(
                "/search", params=params, max_results=max_results, order_by=None
            )
        )

    # --- folders ----------------------------------------------------------

    FOLDER_METADATA_FIELDS = (
        "id,parent_id,title,created_time,updated_time,user_created_time,"
        "user_updated_time,icon,deleted_time"
    )

    def list_folders(
        self,
        *,
        include_deleted: bool = False,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"fields": fields or self.FOLDER_METADATA_FIELDS}
        if include_deleted:
            params["include_deleted"] = 1
        return list(self._paginate("/folders", params=params, max_results=max_results))

    def get_folder(
        self,
        folder_id: str,
        *,
        include_deleted: bool = False,
        fields: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"fields": fields or self.FOLDER_METADATA_FIELDS}
        if include_deleted:
            params["include_deleted"] = 1
        try:
            return self._request("GET", f"/folders/{folder_id}", params=params)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def list_folder_notes(
        self,
        folder_id: str,
        *,
        include_deleted: bool = False,
        include_conflicts: bool = False,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": fields or self._NOTE_LIST_FIELDS + ",is_conflict,deleted_time"
        }
        if include_deleted:
            params["include_deleted"] = 1
        if include_conflicts:
            params["include_conflicts"] = 1
        return list(
            self._paginate(
                f"/folders/{folder_id}/notes", params=params, max_results=max_results
            )
        )

    def create_folder(self, *, title: str, parent_id: str = "") -> dict[str, Any]:
        return self._request("POST", "/folders", payload={"title": title, "parent_id": parent_id})

    def update_folder(self, folder_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/folders/{folder_id}", payload=fields)

    def delete_folder(self, folder_id: str, *, permanent: bool = False) -> None:
        params = {"permanent": 1} if permanent else None
        self._request("DELETE", f"/folders/{folder_id}", params=params)

    def restore_folder(self, folder_id: str) -> dict[str, Any]:
        return self._request("PUT", f"/folders/{folder_id}", payload={"deleted_time": 0})

    # --- tags -------------------------------------------------------------

    TAG_METADATA_FIELDS = "id,title,created_time,updated_time,user_created_time,user_updated_time"

    def list_tags(
        self, *, fields: str | None = None, max_results: int | None = None
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "/tags",
                params={"fields": fields or self.TAG_METADATA_FIELDS},
                max_results=max_results,
            )
        )

    def get_tag(self, tag_id: str, *, fields: str | None = None) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET", f"/tags/{tag_id}", params={"fields": fields or self.TAG_METADATA_FIELDS}
            )
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def list_tag_notes(
        self,
        tag_id: str,
        *,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                f"/tags/{tag_id}/notes",
                params={"fields": fields or self._NOTE_LIST_FIELDS},
                max_results=max_results,
            )
        )

    def list_note_tags(self, note_id: str) -> list[dict[str, Any]]:
        return list(self._paginate(f"/notes/{note_id}/tags", params={"fields": "id,title"}))

    def create_tag(self, title: str) -> dict[str, Any]:
        return self._request("POST", "/tags", payload={"title": title})

    def update_tag(self, tag_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/tags/{tag_id}", payload=fields)

    def delete_tag(self, tag_id: str) -> None:
        self._request("DELETE", f"/tags/{tag_id}")

    def add_tag_to_note(self, tag_id: str, note_id: str) -> None:
        self._request("POST", f"/tags/{tag_id}/notes", payload={"id": note_id})

    def remove_tag_from_note(self, tag_id: str, note_id: str) -> None:
        self._request("DELETE", f"/tags/{tag_id}/notes/{note_id}")

    # --- resources ----------------------------------------------------------

    RESOURCE_METADATA_FIELDS = (
        "id,title,mime,filename,file_extension,size,created_time,updated_time,"
        "user_created_time,user_updated_time,blob_updated_time,ocr_status,ocr_error"
    )

    def list_resources(
        self, *, fields: str | None = None, max_results: int | None = None
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "/resources",
                params={"fields": fields or self.RESOURCE_METADATA_FIELDS},
                max_results=max_results,
            )
        )

    def list_note_resources(
        self,
        note_id: str,
        *,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                f"/notes/{note_id}/resources",
                params={"fields": fields or self.RESOURCE_METADATA_FIELDS},
                max_results=max_results,
            )
        )

    def get_resource(
        self, resource_id: str, *, fields: str | None = None
    ) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET",
                f"/resources/{resource_id}",
                params={"fields": fields or self.RESOURCE_METADATA_FIELDS},
            )
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def get_resource_file(self, resource_id: str) -> bytes:
        return self._request("GET", f"/resources/{resource_id}/file", raw=True)

    def list_resource_notes(
        self,
        resource_id: str,
        *,
        fields: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        requested_fields = fields or self._NOTE_LIST_FIELDS
        linked = list(
            self._paginate(
                f"/resources/{resource_id}/notes",
                params={"fields": requested_fields},
                max_results=max_results,
            )
        )
        if linked:
            return linked

        # Some Joplin Desktop versions return an empty reverse relation even
        # while /notes/:id/resources resolves the same Markdown link. Fall
        # back to the canonical :/resource-id representation in note bodies.
        scan_fields = requested_fields
        if "body" not in {field.strip() for field in scan_fields.split(",")}:
            scan_fields += ",body"
        matches: list[dict[str, Any]] = []
        requested_keys = [field.strip() for field in requested_fields.split(",")]
        for note in self.list_notes(
            include_deleted=True,
            include_conflicts=True,
            fields=scan_fields,
        ):
            if f":/{resource_id}" not in str(note.get("body") or ""):
                continue
            matches.append({key: note.get(key) for key in requested_keys})
            if max_results is not None and len(matches) >= max_results:
                break
        return matches

    def create_resource(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        props = {"title": title if title is not None else filename}
        body, content_type = self._multipart_resource_body(
            data, filename=filename, mime=mime, props=props
        )
        return self._request("POST", "/resources", body=body, content_type=content_type)

    def update_resource(
        self,
        resource_id: str,
        fields: dict[str, Any],
        *,
        data: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
    ) -> dict[str, Any]:
        if data is None:
            return self._request("PUT", f"/resources/{resource_id}", payload=fields)
        if filename is None or mime is None:
            raise ValueError("filename and mime are required when replacing resource data")
        body, content_type = self._multipart_resource_body(
            data, filename=filename, mime=mime, props=fields
        )
        return self._request(
            "PUT", f"/resources/{resource_id}", body=body, content_type=content_type
        )

    def delete_resource(self, resource_id: str) -> None:
        self._request("DELETE", f"/resources/{resource_id}")

    # --- events / revisions (diagnostics only, see plan amendment 4/9) -------

    def get_events(self, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/events", params=params)

    def list_revisions(self) -> list[dict[str, Any]]:
        return list(self._paginate("/revisions"))

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/revisions/{revision_id}")
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise


def ping_url(base_url: str, *, timeout: float = 2.0) -> bool:
    """True when ``base_url`` answers ``GET /ping`` with the Clipper banner."""
    try:
        with urllib.request.urlopen(f"{base_url}/ping", timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace").strip() == PING_RESPONSE
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def discover_base_url(*, host: str = "127.0.0.1", timeout: float = 2.0) -> str:
    """Probe the documented Clipper port range and return the single match.

    Raises ApiError when zero or more than one Joplin service responds.
    """
    found: list[str] = []
    ports = list(DISCOVERY_PORTS)
    for port in ports:
        url = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(f"{url}/ping", timeout=timeout) as resp:
                if resp.read().decode("utf-8", "replace").strip() == PING_RESPONSE:
                    found.append(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            continue
    if not found:
        raise ApiError(
            f"no Joplin Clipper service found on {host} ports "
            f"{min(ports)}-{max(ports)}; "
            "enable the Web Clipper service in Joplin or pass --base-url"
        )
    if len(found) > 1:
        raise ApiError(
            f"multiple Joplin Clipper services found ({', '.join(found)}); "
            "pass --base-url or JOPLIN_BASE_URL to choose one"
        )
    return found[0]
