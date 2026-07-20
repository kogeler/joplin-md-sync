"""Destructive opt-in MCP checks against a real, local Joplin profile.

Safety contract:
* the Joplin token is read from the repository-root ``token`` file;
* every created notebook/note/tag/resource contains a random run id;
* mutation helpers reject every entity id not created by this process;
* cleanup permanently removes only allowlisted ids created by this process;
* pre-existing notes, notebooks, tags, and resources are verified unchanged.

Run explicitly with ``make test-live``. The normal ``make test`` and CI only
collect ``tests/``, never ``tests_live/``.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from joplin_md_sync.api import JoplinClient  # noqa: E402
from joplin_md_sync.config import build_client  # noqa: E402

TOKEN_FILE = REPO / "token"
MCP_PROTOCOL_VERSION = "2025-06-18"
EXPECTED_MCP_TOOLS = {
    "joplin_list_notebooks", "joplin_get_notebook", "joplin_create_notebook",
    "joplin_update_notebook", "joplin_delete_notebook", "joplin_restore_notebook",
    "joplin_list_notebook_notes", "joplin_list_notes", "joplin_get_note",
    "joplin_create_note", "joplin_update_note", "joplin_delete_note",
    "joplin_restore_note", "joplin_search_notes", "joplin_list_tags",
    "joplin_get_tag", "joplin_create_tag", "joplin_update_tag", "joplin_delete_tag",
    "joplin_list_tag_notes", "joplin_add_tag_to_note", "joplin_remove_tag_from_note",
    "joplin_list_resources", "joplin_get_resource", "joplin_read_resource",
    "joplin_create_resource", "joplin_update_resource", "joplin_delete_resource",
    "joplin_list_note_resources", "joplin_list_resource_notes",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveMcpTest(unittest.TestCase):
    api: JoplinClient
    process: subprocess.Popen[str]
    auth_tmp: tempfile.TemporaryDirectory[str]
    auth_token_file: Path
    auth_token: str
    mcp_url: str
    run_id: str
    notebook_title: str
    initial_notes: dict[str, tuple[object, object]]
    initial_folders: dict[str, tuple[object, ...]]
    initial_tags: dict[str, tuple[object, ...]]
    initial_resources: dict[str, tuple[object, ...]]
    owned_note_ids: set[str]
    owned_folder_ids: set[str]
    owned_tag_ids: set[str]
    owned_resource_ids: set[str]
    request_id: int

    @classmethod
    def setUpClass(cls) -> None:
        if not TOKEN_FILE.is_file():
            raise unittest.SkipTest(f"live Joplin token file not found: {TOKEN_FILE}")
        if TOKEN_FILE.stat().st_mode & 0o077:
            raise RuntimeError(f"live Joplin token file must have mode 0600: {TOKEN_FILE}")

        cls.api = build_client(
            token_file=str(TOKEN_FILE), timeout=5.0, discovery_timeout=0.25
        )
        if not cls.api.ping():
            raise RuntimeError(f"unexpected Joplin ping response from {cls.api.base_url}")
        before = cls.api.list_notes(
            include_deleted=True,
            include_conflicts=True,
            fields="id,updated_time,deleted_time",
        )
        cls.initial_notes = {
            str(note["id"]): (note.get("updated_time"), note.get("deleted_time"))
            for note in before
        }
        cls.initial_folders = {
            str(folder["id"]): (
                folder.get("title"),
                folder.get("parent_id"),
                folder.get("updated_time"),
                folder.get("deleted_time"),
            )
            for folder in cls.api.list_folders(include_deleted=True)
        }
        cls.initial_tags = {
            str(tag["id"]): (tag.get("title"), tag.get("updated_time"))
            for tag in cls.api.list_tags()
        }
        cls.initial_resources = {
            str(resource["id"]): (
                resource.get("title"),
                resource.get("filename"),
                resource.get("mime"),
                resource.get("updated_time"),
            )
            for resource in cls.api.list_resources()
        }
        cls.owned_note_ids = set()
        cls.owned_folder_ids = set()
        cls.owned_tag_ids = set()
        cls.owned_resource_ids = set()
        cls.request_id = 0
        cls.run_id = uuid.uuid4().hex
        cls.notebook_title = f"jms-live-{cls.run_id}"

        cls.auth_tmp = tempfile.TemporaryDirectory(prefix="jms-live-auth-")
        cls.auth_token_file = Path(cls.auth_tmp.name) / "mcp-token"
        cls.auth_token = secrets.token_urlsafe(32)
        cls.auth_token_file.write_text(cls.auth_token + "\n", encoding="utf-8")
        cls.auth_token_file.chmod(0o600)

        port = _free_port()
        cls.mcp_url = f"http://127.0.0.1:{port}/mcp"
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(SRC)
            if not existing_pythonpath
            else str(SRC) + os.pathsep + existing_pythonpath
        )
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "joplin_md_sync",
                "mcp",
                "serve",
                "--token-file",
                str(TOKEN_FILE),
                "--base-url",
                cls.api.base_url,
                "--mcp-port",
                str(port),
                "--auth-token-file",
                str(cls.auth_token_file),
                "--retry-timeout",
                "2",
                "--quiet",
            ],
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            cls._wait_until_ready()
        except Exception:
            cls._stop_process()
            cls.auth_tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        errors: list[str] = []
        try:
            for note in cls.api.list_notes(
                include_deleted=True,
                include_conflicts=True,
                fields="id,title,body",
            ):
                note_id = str(note["id"])
                marker_text = f"{note.get('title', '')}\n{note.get('body', '')}"
                if note_id not in cls.initial_notes and cls.run_id in marker_text:
                    cls.owned_note_ids.add(note_id)
            for folder in cls.api.list_folders(include_deleted=True):
                folder_id = str(folder["id"])
                if (
                    folder_id not in cls.initial_folders
                    and cls.run_id in str(folder.get("title") or "")
                ):
                    cls.owned_folder_ids.add(folder_id)
            for tag in cls.api.list_tags():
                tag_id = str(tag["id"])
                if tag_id not in cls.initial_tags and cls.run_id in str(tag.get("title") or ""):
                    cls.owned_tag_ids.add(tag_id)
            for resource in cls.api.list_resources():
                resource_id = str(resource["id"])
                marker_text = f"{resource.get('title', '')}\n{resource.get('filename', '')}"
                if resource_id not in cls.initial_resources and cls.run_id in marker_text:
                    cls.owned_resource_ids.add(resource_id)
        except Exception as exc:
            errors.append(f"discover partially created live objects: {exc}")

        for note_id in sorted(cls.owned_note_ids):
            try:
                note = cls.api.get_note(note_id, include_deleted=True)
                if note is not None:
                    cls.api.delete_note(note_id, permanent=True)
            except Exception as exc:
                errors.append(f"cleanup note {note_id}: {exc}")
        for resource_id in sorted(cls.owned_resource_ids):
            try:
                resource = cls.api.get_resource(resource_id)
                if resource is not None:
                    cls.api.delete_resource(resource_id)
            except Exception as exc:
                errors.append(f"cleanup resource {resource_id}: {exc}")
        for tag_id in sorted(cls.owned_tag_ids):
            try:
                if cls.api.get_tag(tag_id) is not None:
                    cls.api.delete_tag(tag_id)
            except Exception as exc:
                errors.append(f"cleanup tag {tag_id}: {exc}")
        for folder_id in sorted(cls.owned_folder_ids):
            try:
                folder = cls.api.get_folder(folder_id, include_deleted=True)
                if folder is not None:
                    cls.api.delete_folder(folder_id, permanent=True)
            except Exception as exc:
                errors.append(f"cleanup notebook {folder_id}: {exc}")

        try:
            after = cls.api.list_notes(
                include_deleted=True,
                include_conflicts=True,
                fields="id,updated_time,deleted_time",
            )
            after_initial = {
                str(note["id"]): (note.get("updated_time"), note.get("deleted_time"))
                for note in after
                if str(note["id"]) in cls.initial_notes
            }
            if after_initial != cls.initial_notes:
                errors.append("pre-existing Joplin note metadata changed during live tests")
            after_folders = {
                str(folder["id"]): (
                    folder.get("title"),
                    folder.get("parent_id"),
                    folder.get("updated_time"),
                    folder.get("deleted_time"),
                )
                for folder in cls.api.list_folders(include_deleted=True)
                if str(folder["id"]) in cls.initial_folders
            }
            if after_folders != cls.initial_folders:
                errors.append("pre-existing Joplin notebook metadata changed during live tests")
            after_tags = {
                str(tag["id"]): (tag.get("title"), tag.get("updated_time"))
                for tag in cls.api.list_tags()
                if str(tag["id"]) in cls.initial_tags
            }
            if after_tags != cls.initial_tags:
                errors.append("pre-existing Joplin tag metadata changed during live tests")
            after_resources = {
                str(resource["id"]): (
                    resource.get("title"),
                    resource.get("filename"),
                    resource.get("mime"),
                    resource.get("updated_time"),
                )
                for resource in cls.api.list_resources()
                if str(resource["id"]) in cls.initial_resources
            }
            if after_resources != cls.initial_resources:
                errors.append("pre-existing Joplin resource metadata changed during live tests")
            for label, entity_ids, getter in (
                (
                    "notes",
                    cls.owned_note_ids,
                    lambda entity_id: cls.api.get_note(entity_id, include_deleted=True),
                ),
                (
                    "notebooks",
                    cls.owned_folder_ids,
                    lambda entity_id: cls.api.get_folder(entity_id, include_deleted=True),
                ),
                ("tags", cls.owned_tag_ids, cls.api.get_tag),
                ("resources", cls.owned_resource_ids, cls.api.get_resource),
            ):
                remaining = [entity_id for entity_id in entity_ids if getter(entity_id) is not None]
                if remaining:
                    errors.append(f"MCP cleanup left {label}: {', '.join(remaining)}")
        except Exception as exc:
            errors.append(f"pre-existing note verification: {exc}")

        try:
            cls._stop_process()
        finally:
            cls.auth_tmp.cleanup()
        if errors:
            raise AssertionError("; ".join(errors))

    @classmethod
    def _stop_process(cls) -> None:
        if cls.process.poll() is not None:
            return
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)

    @classmethod
    def _wait_until_ready(cls) -> None:
        deadline = time.monotonic() + 10
        last_error = "MCP process did not accept connections"
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                stdout, stderr = cls.process.communicate()
                raise RuntimeError(
                    f"MCP process exited {cls.process.returncode}: {stdout}\n{stderr}"
                )
            try:
                status, body, _ = cls._request(
                    {"jsonrpc": "2.0", "id": 0, "method": "ping"}
                )
                if status == 200 and body is not None and body.get("result") == {}:
                    return
                last_error = f"unexpected readiness response: {status} {body}"
            except OSError as exc:
                last_error = str(exc)
            time.sleep(0.05)
        raise RuntimeError(last_error)

    @classmethod
    def _request(
        cls,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "POST",
        authorize: bool = True,
        origin: str | None = None,
        url: str | None = None,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if authorize:
            headers["Authorization"] = f"Bearer {cls.auth_token}"
        if origin is not None:
            headers["Origin"] = origin
        if payload is not None and isinstance(payload.get("method"), str):
            headers["Mcp-Method"] = payload["method"]
            params = payload.get("params")
            if payload["method"] == "tools/call" and isinstance(params, dict):
                name = params.get("name")
                if isinstance(name, str):
                    headers["Mcp-Name"] = name
        request = urllib.request.Request(
            url or cls.mcp_url, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return response.status, body, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body = json.loads(raw) if raw else None
            return exc.code, body, dict(exc.headers.items())

    @classmethod
    def _rpc(cls, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        cls.request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": cls.request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        status, body, _ = cls._request(payload)
        if status != 200 or body is None:
            raise AssertionError(f"RPC {method} failed: HTTP {status}: {body}")
        if "error" in body:
            raise AssertionError(f"RPC {method} returned protocol error: {body['error']}")
        return body["result"]

    @classmethod
    def _tool(
        cls, name: str, arguments: dict[str, Any], *, expect_error: str | None = None
    ) -> dict[str, Any]:
        result = cls._rpc("tools/call", {"name": name, "arguments": arguments})
        if expect_error is not None:
            if not result.get("isError"):
                raise AssertionError(f"{name} unexpectedly succeeded: {result}")
            error = result["structuredContent"]["error"]
            if error["code"] != expect_error:
                raise AssertionError(f"{name}: expected {expect_error}, got {error}")
            return error
        if result.get("isError"):
            raise AssertionError(f"{name} failed: {result['structuredContent']}")
        structured = result["structuredContent"]
        if json.loads(result["content"][0]["text"]) != structured:
            raise AssertionError(f"{name} text and structured content differ")
        return structured

    @classmethod
    def _create_owned_note(cls, **overrides: object) -> dict[str, Any]:
        arguments: dict[str, object] = {
            "title": f"jms-live-note-{cls.run_id}",
            "body": f"jms live body {cls.run_id}",
            "notebook_title": cls.notebook_title,
            "tags": [f"jms-live-a-{cls.run_id}", f"jms-live-b-{cls.run_id}"],
        }
        arguments.update(overrides)
        if "parent_id" in overrides and "notebook_title" not in overrides:
            arguments.pop("notebook_title", None)
        if "body_html" in overrides and "body" not in overrides:
            arguments.pop("body", None)
        note = cls._tool("joplin_create_note", arguments)["note"]
        note_id = str(note["id"])
        if note_id in cls.initial_notes:
            raise AssertionError(f"create returned a pre-existing note id: {note_id}")
        cls.owned_note_ids.add(note_id)
        notebook = note["metadata"].get("notebook")
        if not isinstance(notebook, dict):
            raise AssertionError(f"note was created in an unexpected notebook: {notebook}")
        if "notebook_title" in arguments and notebook.get("title") != arguments["notebook_title"]:
            raise AssertionError(f"note was created in an unexpected notebook: {notebook}")
        if "parent_id" in arguments and notebook.get("id") != arguments["parent_id"]:
            raise AssertionError(f"note was created in an unexpected notebook: {notebook}")
        folder_id = str(notebook["id"])
        if folder_id in cls.initial_folders:
            raise AssertionError(f"random live notebook unexpectedly pre-existed: {folder_id}")
        cls.owned_folder_ids.add(folder_id)
        for resource in note["metadata"].get("resources", []):
            resource_id = str(resource.get("id") or "")
            if not resource_id or resource_id in cls.initial_resources:
                raise AssertionError(
                    f"note creation returned an invalid or pre-existing resource: {resource_id}"
                )
            cls.owned_resource_ids.add(resource_id)
        return note

    @classmethod
    def _update_owned_note(cls, note_id: str, **fields: object) -> dict[str, Any]:
        if note_id not in cls.owned_note_ids:
            raise AssertionError(f"refusing to update non-owned note: {note_id}")
        return cls._tool(
            "joplin_update_note", {"note_id": note_id, **fields}
        )["note"]

    @classmethod
    def _delete_owned_note(cls, note_id: str) -> dict[str, Any]:
        if note_id not in cls.owned_note_ids:
            raise AssertionError(f"refusing to delete non-owned note: {note_id}")
        return cls._tool("joplin_delete_note", {"note_id": note_id})

    @classmethod
    def _restore_owned_note(cls, note_id: str) -> dict[str, Any]:
        if note_id not in cls.owned_note_ids:
            raise AssertionError(f"refusing to restore non-owned note: {note_id}")
        return cls._tool("joplin_restore_note", {"note_id": note_id})

    @classmethod
    def _create_owned_notebook(cls, title_suffix: str) -> dict[str, Any]:
        notebook = cls._tool(
            "joplin_create_notebook",
            {"title": f"jms-live-{title_suffix}-{cls.run_id}"},
        )["notebook"]
        notebook_id = str(notebook["id"])
        if notebook_id in cls.initial_folders:
            raise AssertionError(f"create returned a pre-existing notebook id: {notebook_id}")
        cls.owned_folder_ids.add(notebook_id)
        return notebook

    @classmethod
    def _update_owned_notebook(cls, notebook_id: str, **fields: object) -> dict[str, Any]:
        if notebook_id not in cls.owned_folder_ids:
            raise AssertionError(f"refusing to update non-owned notebook: {notebook_id}")
        return cls._tool(
            "joplin_update_notebook", {"notebook_id": notebook_id, **fields}
        )["notebook"]

    @classmethod
    def _delete_owned_notebook(cls, notebook_id: str) -> dict[str, Any]:
        if notebook_id not in cls.owned_folder_ids:
            raise AssertionError(f"refusing to delete non-owned notebook: {notebook_id}")
        return cls._tool("joplin_delete_notebook", {"notebook_id": notebook_id})

    @classmethod
    def _restore_owned_notebook(cls, notebook_id: str) -> dict[str, Any]:
        if notebook_id not in cls.owned_folder_ids:
            raise AssertionError(f"refusing to restore non-owned notebook: {notebook_id}")
        return cls._tool("joplin_restore_notebook", {"notebook_id": notebook_id})

    @classmethod
    def _create_owned_tag(cls, title_suffix: str) -> dict[str, Any]:
        result = cls._tool(
            "joplin_create_tag", {"title": f"jms-live-{title_suffix}-{cls.run_id}"}
        )
        tag = result["tag"]
        tag_id = str(tag["id"])
        if not result["created"] or tag_id in cls.initial_tags:
            raise AssertionError(f"create returned a pre-existing tag id: {tag_id}")
        cls.owned_tag_ids.add(tag_id)
        return tag

    @classmethod
    def _update_owned_tag(cls, tag_id: str, title: str) -> dict[str, Any]:
        if tag_id not in cls.owned_tag_ids:
            raise AssertionError(f"refusing to update non-owned tag: {tag_id}")
        return cls._tool("joplin_update_tag", {"tag_id": tag_id, "title": title})["tag"]

    @classmethod
    def _delete_owned_tag(cls, tag_id: str) -> dict[str, Any]:
        if tag_id not in cls.owned_tag_ids:
            raise AssertionError(f"refusing to delete non-owned tag: {tag_id}")
        return cls._tool("joplin_delete_tag", {"tag_id": tag_id})

    @classmethod
    def _create_owned_resource(
        cls, *, filename: str, mime: str, data: bytes
    ) -> dict[str, Any]:
        resource = cls._tool(
            "joplin_create_resource",
            {
                "filename": filename,
                "mime": mime,
                "title": filename,
                "content_base64": base64.b64encode(data).decode("ascii"),
            },
        )["resource"]
        resource_id = str(resource["id"])
        if resource_id in cls.initial_resources:
            raise AssertionError(f"create returned a pre-existing resource id: {resource_id}")
        cls.owned_resource_ids.add(resource_id)
        return resource

    @classmethod
    def _update_owned_resource(cls, resource_id: str, **fields: object) -> dict[str, Any]:
        if resource_id not in cls.owned_resource_ids:
            raise AssertionError(f"refusing to update non-owned resource: {resource_id}")
        return cls._tool(
            "joplin_update_resource", {"resource_id": resource_id, **fields}
        )["resource"]

    @classmethod
    def _delete_owned_resource(cls, resource_id: str) -> dict[str, Any]:
        if resource_id not in cls.owned_resource_ids:
            raise AssertionError(f"refusing to delete non-owned resource: {resource_id}")
        return cls._tool("joplin_delete_resource", {"resource_id": resource_id})

    def test_01_lifecycle_tools_auth_and_transport(self) -> None:
        status, _, _ = self._request(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, authorize=False
        )
        self.assertEqual(status, 401)

        initialized = self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "joplin-md-sync-live", "version": "1"},
            },
        )
        self.assertEqual(initialized["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(self._rpc("ping"), {})
        tools = self._rpc("tools/list", {})["tools"]
        self.assertEqual({tool["name"] for tool in tools}, EXPECTED_MCP_TOOLS)

        status, body, _ = self._request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertEqual((status, body), (202, None))
        status, _, _ = self._request(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        status, body, _ = self._request(
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            origin="http://127.0.0.1:9999",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], {})
        status, _, headers = self._request(method="GET")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")

    def test_02_owned_note_crud_metadata_tags_search_and_trash(self) -> None:
        suffix = self.run_id
        search_token = f"jmss{secrets.token_hex(4)}"
        created = self._create_owned_note(
            title=f"jms live acceptance {suffix}",
            body=f"real MCP create sentinel {suffix} {search_token}",
            author="joplin-md-sync live test",
            source_url="https://example.invalid/jms-live",
            latitude=60.1699,
            longitude=24.9384,
            altitude=12.0,
        )
        note_id = str(created["id"])
        self.assertEqual(
            created["metadata"]["tags"],
            [f"jms-live-a-{suffix}", f"jms-live-b-{suffix}"],
        )

        read = self._tool("joplin_get_note", {"note_id": note_id})["note"]
        self.assertEqual(
            read["body"], f"real MCP create sentinel {suffix} {search_token}"
        )
        self.assertEqual(read["metadata"]["author"], "joplin-md-sync live test")
        self.assertEqual(read["metadata"]["source_url"], "https://example.invalid/jms-live")

        hits: list[dict[str, Any]] = []
        for _ in range(60):
            hits = self._tool(
                "joplin_search_notes",
                {"query": f"id:{note_id}", "limit": 100},
            )["notes"]
            if any(str(note["id"]) == note_id for note in hits):
                break
            time.sleep(0.5)
        self.assertTrue(any(str(note["id"]) == note_id for note in hits), hits)

        move_target = self._create_owned_note(
            title=f"jms live move target {suffix}",
            body=f"temporary move target {suffix}",
            notebook_title=f"{self.notebook_title}-move-target",
            tags=[f"jms-move-{suffix}"],
        )
        move_target_id = str(move_target["id"])
        move_folder_id = str(move_target["metadata"]["notebook"]["id"])
        notebooks = self._tool("joplin_list_notebooks", {})["notebooks"]
        self.assertTrue(any(str(item["id"]) == move_folder_id for item in notebooks))

        due = 1893456000000
        updated = self._update_owned_note(
            note_id,
            title=f"jms live updated {suffix} {search_token}",
            body=f"updated searchable MCP sentinel {suffix} {search_token}",
            parent_id=move_folder_id,
            tags=[f"jms-updated-{suffix}"],
            author="updated live MCP agent",
            is_todo=True,
            todo_due=due,
            todo_completed=0,
            user_updated_time=1890000000000,
        )
        self.assertEqual(updated["metadata"]["tags"], [f"jms-updated-{suffix}"])
        self.assertEqual(updated["metadata"]["is_todo"], 1)
        self.assertEqual(updated["metadata"]["todo_due"], due)
        self.assertEqual(updated["metadata"]["notebook"]["id"], move_folder_id)

        listed = self._tool("joplin_list_notes", {"limit": 100})["notes"]
        self.assertTrue(any(str(note["id"]) == note_id for note in listed))

        deleted = self._delete_owned_note(note_id)
        self.assertFalse(deleted["already_trashed"])
        trashed = self._tool("joplin_get_note", {"note_id": note_id})["note"]
        self.assertGreater(trashed["metadata"]["deleted_time"], 0)
        default_list = self._tool("joplin_list_notes", {"limit": 100})["notes"]
        self.assertFalse(any(str(note["id"]) == note_id for note in default_list))
        deleted_list = self._tool(
            "joplin_list_notes", {"limit": 100, "include_deleted": True}
        )["notes"]
        self.assertTrue(any(str(note["id"]) == note_id for note in deleted_list))
        self.assertTrue(self._delete_owned_note(note_id)["already_trashed"])
        restored = self._restore_owned_note(note_id)
        self.assertFalse(restored["already_active"])
        self.assertEqual(restored["note"]["metadata"]["deleted_time"], 0)
        self.assertFalse(self._delete_owned_note(note_id)["already_trashed"])
        self.assertFalse(self._delete_owned_note(move_target_id)["already_trashed"])

    def test_03_notebooks_tags_resources_html_and_attachments(self) -> None:
        parent = self._create_owned_notebook("parent")
        parent_id = str(parent["id"])
        child = self._tool(
            "joplin_create_notebook",
            {
                "title": f"jms-live-child-{self.run_id}",
                "parent_id": parent_id,
            },
        )["notebook"]
        child_id = str(child["id"])
        self.assertNotIn(child_id, self.initial_folders)
        self.owned_folder_ids.add(child_id)
        self.assertEqual(
            self._tool("joplin_get_notebook", {"notebook_id": child_id})["notebook"]["id"],
            child_id,
        )
        renamed_child = self._update_owned_notebook(
            child_id, title=f"jms-live-child-renamed-{self.run_id}"
        )
        self.assertIn(self.run_id, renamed_child["title"])
        notebooks = self._tool(
            "joplin_list_notebooks", {"limit": 100, "include_deleted": True}
        )["notebooks"]
        self.assertTrue(any(str(notebook["id"]) == child_id for notebook in notebooks))

        entity_note = self._create_owned_note(
            title=f"jms live entities {self.run_id}",
            body_html=f"<p>HTML live content <strong>{self.run_id}</strong></p>",
            parent_id=child_id,
            tags=[],
        )
        entity_note_id = str(entity_note["id"])
        self.assertIn(self.run_id, entity_note["body"])
        notebook_notes = self._tool(
            "joplin_list_notebook_notes", {"notebook_id": child_id, "limit": 100}
        )["notes"]
        self.assertTrue(any(str(note["id"]) == entity_note_id for note in notebook_notes))

        tag = self._create_owned_tag("tag")
        tag_id = str(tag["id"])
        self.assertEqual(self._tool("joplin_get_tag", {"tag_id": tag_id})["tag"]["id"], tag_id)
        renamed_tag = self._update_owned_tag(
            tag_id, f"jms-live-tag-renamed-{self.run_id}"
        )
        self.assertIn(self.run_id, renamed_tag["title"])
        tags = self._tool("joplin_list_tags", {"limit": 100})["tags"]
        self.assertTrue(any(str(item["id"]) == tag_id for item in tags))
        attached = self._tool(
            "joplin_add_tag_to_note", {"tag_id": tag_id, "note_id": entity_note_id}
        )
        self.assertFalse(attached["already_attached"])
        tag_notes = self._tool(
            "joplin_list_tag_notes", {"tag_id": tag_id, "limit": 100}
        )["notes"]
        self.assertTrue(any(str(note["id"]) == entity_note_id for note in tag_notes))
        removed = self._tool(
            "joplin_remove_tag_from_note", {"tag_id": tag_id, "note_id": entity_note_id}
        )
        self.assertTrue(removed["was_attached"])
        self.assertTrue(self._delete_owned_tag(tag_id)["permanent"])

        original_data = f"resource data {self.run_id}".encode()
        resource = self._create_owned_resource(
            filename=f"jms-live-resource-{self.run_id}.txt",
            mime="text/plain",
            data=original_data,
        )
        resource_id = str(resource["id"])
        self.assertEqual(
            self._tool("joplin_get_resource", {"resource_id": resource_id})["resource"]["id"],
            resource_id,
        )
        read_resource = self._tool(
            "joplin_read_resource", {"resource_id": resource_id}
        )
        self.assertEqual(base64.b64decode(read_resource["content_base64"]), original_data)
        replacement_data = f"replacement resource {self.run_id}".encode()
        updated_resource = self._update_owned_resource(
            resource_id,
            title=f"jms-live-resource-updated-{self.run_id}",
            content_base64=base64.b64encode(replacement_data).decode("ascii"),
        )
        self.assertIn(self.run_id, updated_resource["title"])
        resources = self._tool("joplin_list_resources", {"limit": 100})["resources"]
        self.assertTrue(any(str(item["id"]) == resource_id for item in resources))
        linked = self._update_owned_note(
            entity_note_id,
            body=f"resource link {self.run_id}\n\n[attachment](:/{resource_id})",
        )
        self.assertIn(resource_id, linked["body"])
        note_resources = self._tool(
            "joplin_list_note_resources", {"note_id": entity_note_id, "limit": 100}
        )["resources"]
        self.assertTrue(any(str(item["id"]) == resource_id for item in note_resources))
        resource_notes: list[dict[str, Any]] = []
        for _ in range(20):
            resource_notes = self._tool(
                "joplin_list_resource_notes", {"resource_id": resource_id, "limit": 100}
            )["notes"]
            if any(str(note["id"]) == entity_note_id for note in resource_notes):
                break
            time.sleep(0.25)
        self.assertTrue(any(str(note["id"]) == entity_note_id for note in resource_notes))
        self.assertTrue(self._delete_owned_resource(resource_id)["permanent"])

        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        attached_note = self._create_owned_note(
            title=f"jms live attached note {self.run_id}",
            body=f"binary attachment {self.run_id}",
            parent_id=child_id,
            tags=[],
            attachments=[
                {
                    "filename": f"jms-live-image-{self.run_id}.png",
                    "mime": "image/png",
                    "alt_text": "live image",
                    "content_base64": base64.b64encode(png_data).decode("ascii"),
                }
            ],
        )
        self.assertIn("![live image](:/", attached_note["body"])
        self.assertEqual(len(attached_note["metadata"]["resources"]), 1)

        disposable = self._create_owned_notebook("trash")
        disposable_id = str(disposable["id"])
        self.assertFalse(self._delete_owned_notebook(disposable_id)["already_trashed"])
        restored = self._restore_owned_notebook(disposable_id)
        self.assertFalse(restored["already_active"])

    def test_04_tool_errors_do_not_mutate_existing_entities(self) -> None:
        missing_id = uuid.uuid4().hex
        with self.assertRaisesRegex(AssertionError, "refusing to update non-owned note"):
            self._update_owned_note(missing_id, title="must not be sent")
        with self.assertRaisesRegex(AssertionError, "refusing to delete non-owned note"):
            self._delete_owned_note(missing_id)
        with self.assertRaisesRegex(AssertionError, "refusing to update non-owned notebook"):
            self._update_owned_notebook(missing_id, title="must not be sent")
        with self.assertRaisesRegex(AssertionError, "refusing to delete non-owned tag"):
            self._delete_owned_tag(missing_id)
        with self.assertRaisesRegex(AssertionError, "refusing to delete non-owned resource"):
            self._delete_owned_resource(missing_id)
        self._tool(
            "joplin_get_note", {"note_id": missing_id}, expect_error="NOTE_NOT_FOUND"
        )
        self._tool(
            "joplin_create_note",
            {"title": f"invalid-{self.run_id}", "parent_id": missing_id},
            expect_error="NOTEBOOK_NOT_FOUND",
        )
        self._tool(
            "joplin_update_note",
            {"note_id": missing_id},
            expect_error="INVALID_ARGUMENT",
        )
        self._tool(
            "joplin_create_notebook",
            {"title": f"invalid-icon-{self.run_id}", "icon": "fas fa-book"},
            expect_error="INVALID_ARGUMENT",
        )

        self.request_id += 1
        status, body, _ = self._request(
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["code"], -32602)

    def test_05_unavailable_upstream_returns_error_without_stopping_mcp(self) -> None:
        port = _free_port()
        url = f"http://127.0.0.1:{port}/mcp"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "joplin_md_sync",
                "mcp",
                "serve",
                "--token-file",
                str(TOKEN_FILE),
                "--base-url",
                "http://127.0.0.1:1",
                "--mcp-port",
                str(port),
                "--retry-timeout",
                "0",
                "--quiet",
            ],
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    status, body, _ = self._request(
                        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        authorize=False,
                        url=url,
                    )
                    if status == 200 and body["result"] == {}:
                        break
                except OSError:
                    pass
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate()
                    self.fail(f"unavailable-upstream MCP did not start: {stdout}\n{stderr}")
                time.sleep(0.05)

            status, body, _ = self._request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "joplin_list_notes", "arguments": {}},
                },
                authorize=False,
                url=url,
            )
            self.assertEqual(status, 200)
            error = body["result"]["structuredContent"]["error"]
            self.assertEqual(error["code"], "API_UNAVAILABLE")
            self.assertTrue(error["retryable"])

            status, body, _ = self._request(
                {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                authorize=False,
                url=url,
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["result"], {})
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
