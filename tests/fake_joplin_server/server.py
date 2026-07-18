"""In-memory fake of the Joplin Data API for the test suite.

Behavioral fidelity that matters for sync correctness:
* pagination with ``limit``/``page``/``has_more`` and ``order_by``;
* ``PUT`` merges only the supplied fields;
* note ``updated_time`` bumps on note writes but NOT on tag operations;
* ``DELETE`` moves to trash (``deleted_time``) unless ``permanent=1``;
* trashed/conflict notes are hidden unless ``include_deleted``/
  ``include_conflicts`` is passed;
* every endpoint except ``/ping`` requires the token.

Failure injection: set ``server.before_request`` to a callable
``(method, path, query) -> "abort" | None``. Returning ``"abort"`` closes the
connection without a response (client sees a network error). The callable may
also mutate the store (e.g. to simulate a concurrent editor) or reset itself.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeStore:
    """Mutable in-memory Joplin data."""

    def __init__(self) -> None:
        self.notes: dict[str, dict[str, Any]] = {}
        self.folders: dict[str, dict[str, Any]] = {}
        self.tags: dict[str, dict[str, Any]] = {}
        self.note_tags: set[tuple[str, str]] = set()  # (tag_id, note_id)
        self.resources: dict[str, dict[str, Any]] = {}
        self.resource_files: dict[str, bytes] = {}
        self._clock = 1_700_000_000_000

    def tick(self) -> int:
        self._clock += 1000
        return self._clock

    # --- seeding helpers -------------------------------------------------

    def add_folder(self, title: str, parent_id: str = "", folder_id: str | None = None) -> str:
        fid = folder_id or uuid.uuid4().hex
        self.folders[fid] = {
            "id": fid, "title": title, "parent_id": parent_id, "updated_time": self.tick(),
        }
        return fid

    def add_note(
        self,
        title: str,
        body: str,
        parent_id: str,
        note_id: str | None = None,
        *,
        is_conflict: int = 0,
        deleted_time: int = 0,
    ) -> str:
        nid = note_id or uuid.uuid4().hex
        timestamp = self.tick()
        self.notes[nid] = {
            "id": nid, "title": title, "body": body, "parent_id": parent_id,
            "created_time": timestamp, "updated_time": timestamp, "is_conflict": is_conflict,
            "deleted_time": deleted_time,
        }
        return nid

    def add_tag(self, title: str, tag_id: str | None = None) -> str:
        tid = tag_id or uuid.uuid4().hex
        self.tags[tid] = {"id": tid, "title": title.strip().lower()}
        return tid

    def tag_note(self, tag_id: str, note_id: str) -> None:
        self.note_tags.add((tag_id, note_id))

    def add_resource(
        self, data: bytes, *, mime: str = "image/png", filename: str = "",
        title: str | None = None,
        resource_id: str | None = None,
    ) -> str:
        rid = resource_id or uuid.uuid4().hex
        timestamp = self.tick()
        self.resources[rid] = {
            "id": rid,
            "title": title if title is not None else filename or rid,
            "mime": mime,
            "filename": filename,
            "size": len(data),
            "created_time": timestamp,
            "updated_time": timestamp,
        }
        self.resource_files[rid] = data
        return rid

    def note_tag_titles(self, note_id: str) -> list[str]:
        return sorted(
            self.tags[tid]["title"] for tid, nid in self.note_tags if nid == note_id and tid in self.tags
        )


class _Handler(BaseHTTPRequestHandler):
    server: FakeJoplinServer  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # silence test noise
        pass

    # --- plumbing --------------------------------------------------------

    def _reply(self, status: int, payload: Any = None, raw: bytes | None = None) -> None:
        self.send_response(status)
        if raw is not None:
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        body = json.dumps(payload if payload is not None else {}).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_multipart(self) -> tuple[bytes, dict[str, Any], str, str]:
        content_type = self.headers.get("Content-Type") or ""
        marker = "boundary="
        if marker not in content_type:
            raise ValueError("multipart boundary missing")
        boundary = content_type.split(marker, 1)[1].strip().strip('"').encode("ascii")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        data = b""
        props: dict[str, Any] = {}
        filename = ""
        mime = "application/octet-stream"
        for part in raw.split(b"--" + boundary):
            headers_raw, separator, content = part.partition(b"\r\n\r\n")
            if not separator:
                continue
            content = content.removesuffix(b"\r\n")
            headers = headers_raw.decode("utf-8", "replace")
            if 'name="data"' in headers:
                data = content
                for segment in headers.split(";"):
                    segment = segment.strip()
                    if segment.startswith("filename="):
                        filename = segment.split("=", 1)[1].strip().strip('"')
                for line in headers.splitlines():
                    if line.lower().startswith("content-type:"):
                        mime = line.split(":", 1)[1].strip()
            elif 'name="props"' in headers:
                props = json.loads(content.decode("utf-8"))
        return data, props, filename, mime

    def _handle(self, method: str) -> None:
        split = urllib.parse.urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in urllib.parse.parse_qs(split.query).items()}

        hook = self.server.before_request
        if hook is not None and hook(method, path, query) == "abort":
            # Simulate a network failure: close without any response.
            self.connection.close()
            return

        if path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"JoplinClipperServer")
            return

        if query.get("token") != self.server.token:
            self._reply(403, {"error": "Invalid token"})
            return

        store = self.server.store
        parts = [p for p in path.split("/") if p]
        try:
            self._route(method, parts, query, store)
        except KeyError:
            self._reply(404, {"error": f"Not found: {method} {path}"})

    def _paginate(self, items: list[dict[str, Any]], query: dict[str, str]) -> dict[str, Any]:
        order_by = query.get("order_by", "updated_time")
        reverse = query.get("order_dir", "DESC").upper() == "DESC"
        items = sorted(items, key=lambda x: x.get(order_by, 0), reverse=reverse)
        limit = min(int(query.get("limit", 10)), 100)
        page = int(query.get("page", 1))
        start = (page - 1) * limit
        chunk = items[start : start + limit]
        fields = query.get("fields")
        if fields:
            keys = [f.strip() for f in fields.split(",")]
            chunk = [{k: item.get(k) for k in keys if k in item or k in keys} for item in chunk]
        return {"items": chunk, "has_more": start + limit < len(items)}

    # --- routing --------------------------------------------------------

    def _route(
        self, method: str, parts: list[str], query: dict[str, str], store: FakeStore
    ) -> None:
        # /notes ...
        if parts[0] == "notes":
            if len(parts) == 1:
                if method == "GET":
                    items = list(store.notes.values())
                    if not query.get("include_deleted"):
                        items = [n for n in items if not n.get("deleted_time")]
                    if not query.get("include_conflicts"):
                        items = [n for n in items if not n.get("is_conflict")]
                    self._reply(200, self._paginate(items, query))
                    return
                if method == "POST":
                    data = self._read_json()
                    nid = data.get("id") or uuid.uuid4().hex
                    if nid in store.notes:
                        # Real Joplin fails with a UNIQUE-constraint error
                        # when creating a note under an existing id (even a
                        # trashed one); restoring goes via PUT deleted_time=0.
                        self._reply(500, {
                            "error": "Internal Server Error: Error: SQLITE_CONSTRAINT: "
                            "UNIQUE constraint failed: notes.id"
                        })
                        return
                    store.add_note(
                        data.get("title", ""), data.get("body", data.get("body_html", "")),
                        data.get("parent_id", ""), note_id=nid,
                    )
                    for key in (
                        "author", "source_url", "is_todo", "todo_due", "todo_completed",
                        "user_created_time", "user_updated_time", "latitude", "longitude",
                        "altitude", "source", "source_application", "application_data",
                        "user_data", "order", "markup_language",
                    ):
                        if key in data:
                            store.notes[nid][key] = data[key]
                    self._reply(200, store.notes[nid])
                    return
            note = store.notes[parts[1]]
            if len(parts) == 2:
                if method == "GET":
                    # Real Joplin returns trashed notes on single-note GET
                    # regardless of include_deleted (verified live).
                    fields = query.get("fields")
                    if fields:
                        keys = [f.strip() for f in fields.split(",")]
                        self._reply(200, {k: note.get(k) for k in keys})
                    else:
                        self._reply(200, note)
                    return
                if method == "PUT":
                    # Real Joplin applies PUT to trashed notes too, and
                    # accepts deleted_time (=0 restores from trash).
                    data = self._read_json()
                    for key in (
                        "title", "body", "parent_id", "deleted_time", "author", "source_url",
                        "is_todo", "todo_due", "todo_completed", "user_created_time",
                        "user_updated_time", "latitude", "longitude", "altitude",
                        "source", "source_application", "application_data", "user_data",
                        "order", "markup_language", "body_html", "base_url",
                    ):
                        if key in data:
                            note[key] = data[key]
                    note["updated_time"] = store.tick()
                    self._reply(200, note)
                    return
                if method == "DELETE":
                    if query.get("permanent") == "1":
                        del store.notes[parts[1]]
                    else:
                        note["deleted_time"] = store.tick()
                    self._reply(200, {})
                    return
            if len(parts) == 3 and parts[2] == "tags" and method == "GET":
                tags = [
                    store.tags[tid]
                    for tid, nid in store.note_tags
                    if nid == parts[1] and tid in store.tags
                ]
                self._reply(200, self._paginate(tags, {**query, "order_by": "id", "order_dir": "ASC"}))
                return
            if len(parts) == 3 and parts[2] == "resources" and method == "GET":
                linked = [
                    store.resources[rid]
                    for rid in store.resources
                    if f":/{rid}" in note.get("body", "")
                ]
                self._reply(200, self._paginate(linked, {**query, "order_by": "id", "order_dir": "ASC"}))
                return

        elif parts[0] == "search" and method == "GET":
            raw_query = query.get("query", "").strip().lower()
            terms = [term for term in raw_query.split() if ":" not in term]
            notes = [
                note for note in store.notes.values()
                if not note.get("deleted_time")
                and not note.get("is_conflict")
                and all(
                    term in f"{note.get('title', '')}\n{note.get('body', '')}".lower()
                    for term in terms
                )
            ]
            self._reply(200, self._paginate(notes, query))
            return

        elif parts[0] == "folders":
            if len(parts) == 1:
                if method == "GET":
                    folders = list(store.folders.values())
                    if not query.get("include_deleted"):
                        folders = [folder for folder in folders if not folder.get("deleted_time")]
                    self._reply(200, self._paginate(folders, query))
                    return
                if method == "POST":
                    data = self._read_json()
                    fid = store.add_folder(data.get("title", ""), data.get("parent_id", ""))
                    for key in ("icon", "user_created_time", "user_updated_time"):
                        if key in data:
                            store.folders[fid][key] = data[key]
                    self._reply(200, store.folders[fid])
                    return
            folder = store.folders[parts[1]]
            if len(parts) == 2:
                if method == "GET":
                    fields = query.get("fields")
                    if fields:
                        keys = [field.strip() for field in fields.split(",")]
                        self._reply(200, {key: folder.get(key) for key in keys})
                    else:
                        self._reply(200, folder)
                    return
                if method == "PUT":
                    data = self._read_json()
                    for key in (
                        "title", "parent_id", "icon", "user_created_time",
                        "user_updated_time", "deleted_time",
                    ):
                        if key in data:
                            folder[key] = data[key]
                    folder["updated_time"] = store.tick()
                    self._reply(200, folder)
                    return
                if method == "DELETE":
                    if query.get("permanent") == "1":
                        del store.folders[parts[1]]
                    else:
                        folder["deleted_time"] = store.tick()
                    self._reply(200, {})
                    return
            if len(parts) == 3 and parts[2] == "notes" and method == "GET":
                notes = [
                    note for note in store.notes.values()
                    if note.get("parent_id") == parts[1]
                ]
                if not query.get("include_deleted"):
                    notes = [note for note in notes if not note.get("deleted_time")]
                if not query.get("include_conflicts"):
                    notes = [note for note in notes if not note.get("is_conflict")]
                self._reply(200, self._paginate(notes, query))
                return

        elif parts[0] == "tags":
            if len(parts) == 1:
                if method == "GET":
                    self._reply(200, self._paginate(list(store.tags.values()), {**query, "order_by": "id", "order_dir": "ASC"}))
                    return
                if method == "POST":
                    data = self._read_json()
                    title = (data.get("title") or "").strip().lower()
                    for tag in store.tags.values():
                        if tag["title"] == title:
                            self._reply(200, tag)
                            return
                    tid = store.add_tag(title)
                    self._reply(200, store.tags[tid])
                    return
            if len(parts) == 2 and method == "GET":
                tag = store.tags[parts[1]]
                fields = query.get("fields")
                if fields:
                    keys = [field.strip() for field in fields.split(",")]
                    self._reply(200, {key: tag.get(key) for key in keys})
                else:
                    self._reply(200, tag)
                return
            if len(parts) == 3 and parts[2] == "notes":
                if method == "GET":
                    notes = [
                        store.notes[nid]
                        for tid, nid in store.note_tags
                        if tid == parts[1] and nid in store.notes
                    ]
                    self._reply(200, self._paginate(notes, {**query, "order_by": "id", "order_dir": "ASC"}))
                    return
                if method == "POST":
                    data = self._read_json()
                    _ = store.tags[parts[1]]
                    store.note_tags.add((parts[1], data["id"]))
                    self._reply(200, {})
                    return
            if len(parts) == 4 and parts[2] == "notes" and method == "DELETE":
                store.note_tags.discard((parts[1], parts[3]))
                self._reply(200, {})
                return
            if len(parts) == 2 and method == "DELETE":
                del store.tags[parts[1]]
                store.note_tags = {
                    (tag_id, note_id)
                    for tag_id, note_id in store.note_tags
                    if tag_id != parts[1]
                }
                self._reply(200, {})
                return
            if len(parts) == 2 and method == "PUT":
                data = self._read_json()
                if "title" in data:
                    store.tags[parts[1]]["title"] = str(data["title"]).strip().lower()
                store.tags[parts[1]]["updated_time"] = store.tick()
                self._reply(200, store.tags[parts[1]])
                return

        elif parts[0] == "resources":
            if len(parts) == 1:
                if method == "GET":
                    self._reply(200, self._paginate(list(store.resources.values()), query))
                    return
                if method == "POST":
                    data, props, filename, mime = self._read_multipart()
                    rid = store.add_resource(
                        data,
                        filename=filename,
                        mime=mime,
                        title=str(props.get("title") or filename),
                    )
                    self._reply(200, store.resources[rid])
                    return
            resource = store.resources[parts[1]]
            if len(parts) == 2:
                if method == "GET":
                    fields = query.get("fields")
                    if fields:
                        keys = [field.strip() for field in fields.split(",")]
                        self._reply(200, {key: resource.get(key) for key in keys})
                    else:
                        self._reply(200, resource)
                    return
                if method == "PUT":
                    if (self.headers.get("Content-Type") or "").startswith("multipart/form-data"):
                        data, props, filename, mime = self._read_multipart()
                        store.resource_files[parts[1]] = data
                        resource.update(props)
                        resource.update({"filename": filename, "mime": mime, "size": len(data)})
                    else:
                        resource.update(self._read_json())
                    resource["updated_time"] = store.tick()
                    self._reply(200, resource)
                    return
                if method == "DELETE":
                    del store.resources[parts[1]]
                    store.resource_files.pop(parts[1], None)
                    self._reply(200, {})
                    return
            if len(parts) == 3 and parts[2] == "file" and method == "GET":
                self._reply(200, raw=store.resource_files[parts[1]])
                return
            if len(parts) == 3 and parts[2] == "notes" and method == "GET":
                # Matches observed Joplin Desktop behavior: the documented
                # reverse relation can be empty while note -> resources works.
                self._reply(200, self._paginate([], query))
                return

        elif parts[0] == "events" and method == "GET":
            self._reply(200, {"items": [], "has_more": False, "cursor": "fake-cursor-1"})
            return

        elif parts[0] == "revisions" and method == "GET":
            self._reply(200, {"items": [], "has_more": False})
            return

        raise KeyError("/".join(parts))

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


class FakeJoplinServer:
    """Threaded fake Joplin server bound to an ephemeral localhost port."""

    def __init__(self, token: str = "test-token", port: int = 0) -> None:
        self.token = token
        self.store = FakeStore()
        self.before_request: Callable[[str, str, dict[str, str]], str | None] | None = None
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.token = token  # type: ignore[attr-defined]
        self._httpd.store = self.store  # type: ignore[attr-defined]
        self._httpd.before_request = None  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeJoplinServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def set_before_request(
        self, hook: Callable[[str, str, dict[str, str]], str | None] | None
    ) -> None:
        self.before_request = hook
        self._httpd.before_request = hook  # type: ignore[attr-defined]

    def __enter__(self) -> FakeJoplinServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
