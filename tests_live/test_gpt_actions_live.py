"""Destructive opt-in GPT Actions checks against a real local Joplin profile.

The suite starts the production CLI listener and exercises the complete HTTP
Actions chain. Every created entity contains a random run id. Teardown scans
for partial creations, permanently removes only ids absent from the initial
snapshot, and verifies that pre-existing entities were not changed.
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
from joplin_md_sync.gpt_openapi import registry_for_export  # noqa: E402

TOKEN_FILE = REPO / "token"
ACTIONS_PREFIX = "/api/gpt/v1/tools"
MCP_PROTOCOL_VERSION = "2025-06-18"
CURRENT_TOKEN = object()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveGptActionsTest(unittest.TestCase):
    api: JoplinClient
    process: subprocess.Popen[str]
    auth_tmp: tempfile.TemporaryDirectory[str]
    actions_token_file: Path
    mcp_token_file: Path
    log_file: Path
    actions_token: str
    mcp_token: str
    joplin_token: str
    base_url: str
    mcp_url: str
    run_id: str
    initial_notes: dict[str, tuple[object, object]]
    initial_folders: dict[str, tuple[object, ...]]
    initial_tags: dict[str, tuple[object, ...]]
    initial_resources: dict[str, tuple[object, ...]]
    owned_note_ids: set[str]
    owned_folder_ids: set[str]
    owned_tag_ids: set[str]
    owned_resource_ids: set[str]
    called_tools: set[str]
    sensitive_values: set[str]
    notebook_id: str
    child_notebook_id: str
    note_id: str
    resource_id: str

    @classmethod
    def setUpClass(cls) -> None:
        if not TOKEN_FILE.is_file():
            raise unittest.SkipTest(f"live Joplin token file not found: {TOKEN_FILE}")
        if os.name == "posix" and TOKEN_FILE.stat().st_mode & 0o077:
            raise RuntimeError(f"live Joplin token file must have mode 0600: {TOKEN_FILE}")

        cls.api = build_client(
            token_file=str(TOKEN_FILE), timeout=5.0, discovery_timeout=0.25
        )
        if not cls.api.ping():
            raise RuntimeError(f"unexpected Joplin ping response from {cls.api.base_url}")
        cls.joplin_token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        cls._snapshot_initial_state()
        cls.owned_note_ids = set()
        cls.owned_folder_ids = set()
        cls.owned_tag_ids = set()
        cls.owned_resource_ids = set()
        cls.called_tools = set()
        cls.run_id = uuid.uuid4().hex

        cls.auth_tmp = tempfile.TemporaryDirectory(prefix="jms-gpt-live-")
        root = Path(cls.auth_tmp.name)
        cls.actions_token_file = root / "actions-token"
        cls.mcp_token_file = root / "mcp-token"
        cls.log_file = root / "actions.log"
        cls.actions_token = secrets.token_urlsafe(32)
        cls.mcp_token = secrets.token_urlsafe(32)
        cls.sensitive_values = {
            cls.actions_token,
            cls.mcp_token,
            cls.joplin_token,
            cls.run_id,
        }
        cls._write_secret(cls.actions_token_file, cls.actions_token)
        cls._write_secret(cls.mcp_token_file, cls.mcp_token)

        cls.process, cls.base_url = cls._spawn_server(
            [
                "--auth-token-file",
                str(cls.mcp_token_file),
                "--gpt-actions-max-request-bytes",
                "4096",
                "--gpt-actions-rate-limit",
                "1000",
                "--retry-timeout",
                "2",
                "--log-file",
                str(cls.log_file),
                "--quiet",
            ]
        )
        cls.mcp_url = f"{cls.base_url}/mcp"
        try:
            cls._wait_until_ready(cls.process, cls.base_url)
        except Exception:
            cls._stop_process(cls.process)
            cls.auth_tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        errors: list[str] = []
        try:
            cls._discover_partial_creations(errors)
            cls._remove_owned_entities(errors)
            cls._verify_initial_state(errors)
            try:
                logs = cls.log_file.read_text(encoding="utf-8")
                if "gpt_actions_request" not in logs:
                    errors.append("Actions metadata log contains no request events")
                for secret in cls.sensitive_values:
                    if secret and secret in logs:
                        errors.append("Actions metadata log contains secret or test content")
                        break
            except OSError as exc:
                errors.append(f"read Actions metadata log: {exc}")
        finally:
            cls._stop_process(cls.process)
            cls.auth_tmp.cleanup()
        if errors:
            raise AssertionError("; ".join(errors))

    @classmethod
    def _snapshot_initial_state(cls) -> None:
        cls.initial_notes = {
            str(note["id"]): (note.get("updated_time"), note.get("deleted_time"))
            for note in cls.api.list_notes(
                include_deleted=True,
                include_conflicts=True,
                fields="id,updated_time,deleted_time",
            )
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

    @classmethod
    def _discover_partial_creations(cls, errors: list[str]) -> None:
        try:
            for note in cls.api.list_notes(
                include_deleted=True,
                include_conflicts=True,
                fields="id,title,body",
            ):
                note_id = str(note["id"])
                marker = f"{note.get('title', '')}\n{note.get('body', '')}"
                if note_id not in cls.initial_notes and cls.run_id in marker:
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
                marker = f"{resource.get('title', '')}\n{resource.get('filename', '')}"
                if resource_id not in cls.initial_resources and cls.run_id in marker:
                    cls.owned_resource_ids.add(resource_id)
        except Exception as exc:
            errors.append(f"discover partially created Actions objects: {exc}")

    @classmethod
    def _remove_owned_entities(cls, errors: list[str]) -> None:
        for note_id in sorted(cls.owned_note_ids):
            try:
                if cls.api.get_note(note_id, include_deleted=True) is not None:
                    cls.api.delete_note(note_id, permanent=True)
            except Exception as exc:
                errors.append(f"cleanup Actions note {note_id}: {exc}")
        for resource_id in sorted(cls.owned_resource_ids):
            try:
                if cls.api.get_resource(resource_id) is not None:
                    cls.api.delete_resource(resource_id)
            except Exception as exc:
                errors.append(f"cleanup Actions resource {resource_id}: {exc}")
        for tag_id in sorted(cls.owned_tag_ids):
            try:
                if cls.api.get_tag(tag_id) is not None:
                    cls.api.delete_tag(tag_id)
            except Exception as exc:
                errors.append(f"cleanup Actions tag {tag_id}: {exc}")
        for folder_id in sorted(cls.owned_folder_ids, reverse=True):
            try:
                if cls.api.get_folder(folder_id, include_deleted=True) is not None:
                    cls.api.delete_folder(folder_id, permanent=True)
            except Exception as exc:
                errors.append(f"cleanup Actions notebook {folder_id}: {exc}")

    @classmethod
    def _verify_initial_state(cls, errors: list[str]) -> None:
        try:
            notes = {
                str(note["id"]): (note.get("updated_time"), note.get("deleted_time"))
                for note in cls.api.list_notes(
                    include_deleted=True,
                    include_conflicts=True,
                    fields="id,updated_time,deleted_time",
                )
                if str(note["id"]) in cls.initial_notes
            }
            folders = {
                str(folder["id"]): (
                    folder.get("title"),
                    folder.get("parent_id"),
                    folder.get("updated_time"),
                    folder.get("deleted_time"),
                )
                for folder in cls.api.list_folders(include_deleted=True)
                if str(folder["id"]) in cls.initial_folders
            }
            tags = {
                str(tag["id"]): (tag.get("title"), tag.get("updated_time"))
                for tag in cls.api.list_tags()
                if str(tag["id"]) in cls.initial_tags
            }
            resources = {
                str(resource["id"]): (
                    resource.get("title"),
                    resource.get("filename"),
                    resource.get("mime"),
                    resource.get("updated_time"),
                )
                for resource in cls.api.list_resources()
                if str(resource["id"]) in cls.initial_resources
            }
            for label, actual, expected in (
                ("notes", notes, cls.initial_notes),
                ("notebooks", folders, cls.initial_folders),
                ("tags", tags, cls.initial_tags),
                ("resources", resources, cls.initial_resources),
            ):
                if actual != expected:
                    errors.append(f"pre-existing Joplin {label} changed during Actions tests")
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
                    errors.append(f"Actions cleanup left {label}: {', '.join(remaining)}")
        except Exception as exc:
            errors.append(f"verify pre-existing state after Actions tests: {exc}")

    @staticmethod
    def _write_secret(path: Path, token: str) -> None:
        path.write_text(token + "\n", encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)

    @classmethod
    def _spawn_server(
        cls,
        extra_args: list[str],
        *,
        base_url: str | None = None,
        token_file: Path = TOKEN_FILE,
    ) -> tuple[subprocess.Popen[str], str]:
        port = _free_port()
        public_url = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(SRC)
            if not existing_pythonpath
            else str(SRC) + os.pathsep + existing_pythonpath
        )
        command = [
            sys.executable,
            "-m",
            "joplin_md_sync",
            "mcp",
            "serve",
            "--token-file",
            str(token_file),
            "--base-url",
            base_url or cls.api.base_url,
            "--mcp-port",
            str(port),
            "--gpt-actions",
            "--gpt-actions-token-file",
            str(cls.actions_token_file),
            *extra_args,
        ]
        return (
            subprocess.Popen(
                command,
                cwd=REPO,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ),
            public_url,
        )

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @classmethod
    def _wait_until_ready(cls, process: subprocess.Popen[str], base_url: str) -> None:
        deadline = time.monotonic() + 10
        last_error = "Actions process did not accept connections"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"Actions process exited {process.returncode}: {stdout}\n{stderr}"
                )
            try:
                status, body, _ = cls._http_request(
                    f"{base_url}/healthz", method="GET", token=None
                )
                if status == 200 and body == {"ok": True}:
                    return
                last_error = f"unexpected Actions readiness response: {status} {body}"
            except OSError as exc:
                last_error = str(exc)
            time.sleep(0.05)
        raise RuntimeError(last_error)

    @classmethod
    def _http_request(
        cls,
        url: str,
        payload: object | None = None,
        *,
        raw: bytes | None = None,
        method: str = "POST",
        token: str | None | object = CURRENT_TOKEN,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        data = raw
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": content_type, **(extra_headers or {})}
        effective_token = cls.actions_token if token is CURRENT_TOKEN else token
        if isinstance(effective_token, str):
            headers["Authorization"] = f"Bearer {effective_token}"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response_body = response.read()
                parsed = json.loads(response_body) if response_body else None
                return response.status, parsed, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            parsed = json.loads(response_body) if response_body else None
            return exc.code, parsed, dict(exc.headers.items())

    @classmethod
    def _request(
        cls,
        tool: str,
        arguments: object,
        *,
        base_url: str | None = None,
        token: str | None | object = CURRENT_TOKEN,
        method: str = "POST",
        raw: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        return cls._http_request(
            f"{base_url or cls.base_url}{ACTIONS_PREFIX}/{tool}",
            arguments,
            raw=raw,
            method=method,
            token=token,
            content_type=content_type,
        )

    @classmethod
    def _action(cls, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        cls.called_tools.add(tool)
        status, body, headers = cls._request(tool, arguments)
        if status != 200 or body is None or not body.get("success"):
            raise AssertionError(f"Action {tool} failed: HTTP {status}: {body}")
        if body.get("request_id") != headers.get("X-Request-ID"):
            raise AssertionError(f"Action {tool} returned inconsistent request ids")
        result = body.get("result")
        if not isinstance(result, dict):
            raise AssertionError(f"Action {tool} returned a non-object result")
        return result

    @classmethod
    def _error(
        cls,
        tool: str,
        arguments: object,
        *,
        status: int,
        code: str,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        actual_status, body, headers = cls._request(tool, arguments, base_url=base_url)
        if actual_status != status or body is None:
            raise AssertionError(
                f"Action {tool}: expected HTTP {status}/{code}, got {actual_status}: {body}"
            )
        if body.get("request_id") != headers.get("X-Request-ID"):
            raise AssertionError(f"Action {tool} error returned inconsistent request ids")
        error = body.get("error")
        if not isinstance(error, dict) or error.get("code") != code:
            raise AssertionError(f"Action {tool}: expected {code}, got {body}")
        return error

    @classmethod
    def _mcp_ping(cls, token: str) -> int:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        request = urllib.request.Request(
            cls.mcp_url,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as exc:
            exc.read()
            return exc.code

    @classmethod
    def _remember_notebook(cls, notebook: dict[str, Any]) -> str:
        notebook_id = str(notebook["id"])
        if notebook_id in cls.initial_folders:
            raise AssertionError("Actions create returned a pre-existing notebook id")
        cls.owned_folder_ids.add(notebook_id)
        return notebook_id

    @classmethod
    def _remember_note(cls, note: dict[str, Any]) -> str:
        note_id = str(note["id"])
        if note_id in cls.initial_notes:
            raise AssertionError("Actions create returned a pre-existing note id")
        cls.owned_note_ids.add(note_id)
        notebook = note.get("metadata", {}).get("notebook")
        if isinstance(notebook, dict):
            cls._remember_notebook(notebook)
        for resource in note.get("metadata", {}).get("resources", []):
            resource_id = str(resource.get("id") or "")
            if not resource_id or resource_id in cls.initial_resources:
                raise AssertionError("Actions create returned an invalid resource id")
            cls.owned_resource_ids.add(resource_id)
        return note_id

    def test_01_transport_auth_rotation_and_route_isolation(self) -> None:
        for path in ("/healthz", "/readyz"):
            status, body, _ = self._http_request(
                f"{self.base_url}{path}", method="GET", token=None
            )
            self.assertEqual((status, body), (200, {"ok": True}))

        for token in (None, "incorrect-actions-token", self.mcp_token, self.joplin_token):
            status, body, headers = self._request(
                "route-that-does-not-exist", {}, token=token
            )
            self.assertEqual(status, 401)
            self.assertEqual(body["error"]["code"], "UNAUTHORIZED")
            self.assertIn("joplin-md-sync-gpt-actions", headers["WWW-Authenticate"])

        self._error(
            "route-that-does-not-exist", {}, status=404, code="ACTION_NOT_FOUND"
        )
        for disabled in (
            "joplin_read_resource",
            "joplin_create_resource",
            "joplin_update_resource",
        ):
            self._error(disabled, {}, status=404, code="ACTION_NOT_FOUND")

        status, body, headers = self._request(
            "joplin_list_notes", {}, method="GET"
        )
        self.assertEqual(status, 405)
        self.assertEqual(body["error"]["code"], "METHOD_NOT_ALLOWED")
        self.assertEqual(headers["Allow"], "POST")
        status, body, headers = self._request(
            "joplin_list_notes", {}, method="HEAD"
        )
        self.assertEqual((status, body, headers["Allow"]), (405, None, "POST"))

        self.assertEqual(
            self._request("joplin_list_notes", {}, content_type="text/plain")[0], 415
        )
        self.assertEqual(
            self._request("joplin_list_notes", {}, raw=b"{")[0], 400
        )
        self.assertEqual(self._request("joplin_list_notes", [])[0], 400)
        self._error(
            "joplin_list_notes",
            {"unexpected": True},
            status=422,
            code="INVALID_ARGUMENT",
        )
        status, body, _ = self._request(
            "joplin_list_notes", {}, raw=b'{"padding":"' + b"x" * 5000 + b'"}'
        )
        self.assertEqual(status, 413)
        self.assertEqual(body["error"]["code"], "REQUEST_TOO_LARGE")

        self.assertEqual(self._mcp_ping(self.actions_token), 401)
        self.assertEqual(self._mcp_ping(self.joplin_token), 401)
        self.assertEqual(self._mcp_ping(self.mcp_token), 200)

        previous = self.actions_token
        replacement = secrets.token_urlsafe(32)
        temporary = self.actions_token_file.with_suffix(".new")
        self._write_secret(temporary, replacement)
        os.replace(temporary, self.actions_token_file)
        type(self).actions_token = replacement
        self.sensitive_values.add(replacement)
        self.assertEqual(
            self._request("joplin_list_notes", {"limit": 1}, token=previous)[0], 401
        )
        self.assertEqual(self._request("joplin_list_notes", {"limit": 1})[0], 200)

    def test_02_all_notebook_and_note_actions(self) -> None:
        parent = self._action(
            "joplin_create_notebook",
            {"title": f"jms-gpt-parent-{self.run_id}"},
        )["notebook"]
        type(self).notebook_id = self._remember_notebook(parent)
        child = self._action(
            "joplin_create_notebook",
            {
                "title": f"jms-gpt-child-{self.run_id}",
                "parent_id": self.notebook_id,
            },
        )["notebook"]
        type(self).child_notebook_id = self._remember_notebook(child)

        fetched = self._action(
            "joplin_get_notebook", {"notebook_id": self.child_notebook_id}
        )["notebook"]
        self.assertEqual(fetched["parent_id"], self.notebook_id)
        renamed = self._action(
            "joplin_update_notebook",
            {
                "notebook_id": self.child_notebook_id,
                "title": f"jms-gpt-child-renamed-{self.run_id}",
            },
        )["notebook"]
        self.assertIn(self.run_id, renamed["title"])
        notebooks = self._action(
            "joplin_list_notebooks", {"limit": 100, "include_deleted": True}
        )["notebooks"]
        self.assertTrue(any(str(item["id"]) == self.child_notebook_id for item in notebooks))

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        search_marker = f"jmsgpt{secrets.token_hex(4)}"
        created = self._action(
            "joplin_create_note",
            {
                "title": f"jms GPT Actions live {self.run_id}",
                "body": f"Actions live body {self.run_id} {search_marker}",
                "parent_id": self.child_notebook_id,
                "tags": [],
                "author": "joplin-md-sync GPT Actions live test",
                "source_url": "https://example.invalid/jms-gpt-live",
                "attachments": [
                    {
                        "filename": f"jms-gpt-resource-{self.run_id}.png",
                        "mime": "image/png",
                        "alt_text": "GPT Actions live image",
                        "content_base64": base64.b64encode(png).decode("ascii"),
                    }
                ],
            },
        )["note"]
        type(self).note_id = self._remember_note(created)
        type(self).resource_id = str(created["metadata"]["resources"][0]["id"])
        self.assertIn(self.resource_id, created["body"])

        fetched_note = self._action("joplin_get_note", {"note_id": self.note_id})["note"]
        self.assertEqual(fetched_note["metadata"]["author"], created["metadata"]["author"])
        updated = self._action(
            "joplin_update_note",
            {
                "note_id": self.note_id,
                "title": f"jms GPT Actions updated {self.run_id}",
                "body": (
                    f"updated Actions live body {self.run_id} {search_marker}\n\n"
                    f"![GPT Actions live image](:/{self.resource_id})"
                ),
                "is_todo": True,
                "todo_due": 1893456000000,
            },
        )["note"]
        self.assertEqual(updated["metadata"]["is_todo"], 1)

        listed = self._action(
            "joplin_list_notes",
            {"limit": 100, "include_deleted": False, "include_conflicts": True},
        )["notes"]
        self.assertTrue(any(str(item["id"]) == self.note_id for item in listed))
        notebook_notes = self._action(
            "joplin_list_notebook_notes",
            {"notebook_id": self.child_notebook_id, "limit": 100},
        )["notes"]
        self.assertTrue(any(str(item["id"]) == self.note_id for item in notebook_notes))

        hits: list[dict[str, Any]] = []
        for _ in range(60):
            hits = self._action(
                "joplin_search_notes", {"query": f"id:{self.note_id}", "limit": 100}
            )["notes"]
            if any(str(item["id"]) == self.note_id for item in hits):
                break
            time.sleep(0.5)
        self.assertTrue(any(str(item["id"]) == self.note_id for item in hits), hits)

        deleted = self._action("joplin_delete_note", {"note_id": self.note_id})
        self.assertFalse(deleted["already_trashed"])
        self.assertTrue(
            self._action("joplin_delete_note", {"note_id": self.note_id})["already_trashed"]
        )
        trashed = self._action("joplin_get_note", {"note_id": self.note_id})["note"]
        self.assertGreater(trashed["metadata"]["deleted_time"], 0)
        restored = self._action("joplin_restore_note", {"note_id": self.note_id})
        self.assertFalse(restored["already_active"])
        self.assertTrue(
            self._action("joplin_restore_note", {"note_id": self.note_id})["already_active"]
        )

        disposable = self._action(
            "joplin_create_notebook", {"title": f"jms-gpt-trash-{self.run_id}"}
        )["notebook"]
        disposable_id = self._remember_notebook(disposable)
        self.assertFalse(
            self._action("joplin_delete_notebook", {"notebook_id": disposable_id})[
                "already_trashed"
            ]
        )
        self.assertTrue(
            self._action("joplin_delete_notebook", {"notebook_id": disposable_id})[
                "already_trashed"
            ]
        )
        self.assertFalse(
            self._action("joplin_restore_notebook", {"notebook_id": disposable_id})[
                "already_active"
            ]
        )

    def test_03_all_tag_and_resource_actions(self) -> None:
        tag_result = self._action(
            "joplin_create_tag", {"title": f"jms-gpt-tag-{self.run_id}"}
        )
        tag = tag_result["tag"]
        tag_id = str(tag["id"])
        self.assertTrue(tag_result["created"])
        self.assertNotIn(tag_id, self.initial_tags)
        self.owned_tag_ids.add(tag_id)
        fetched = self._action("joplin_get_tag", {"tag_id": tag_id})["tag"]
        self.assertEqual(fetched["id"], tag_id)
        renamed = self._action(
            "joplin_update_tag",
            {"tag_id": tag_id, "title": f"jms-gpt-tag-renamed-{self.run_id}"},
        )["tag"]
        self.assertIn(self.run_id, renamed["title"])
        tags = self._action("joplin_list_tags", {"limit": 100})["tags"]
        self.assertTrue(any(str(item["id"]) == tag_id for item in tags))

        attached = self._action(
            "joplin_add_tag_to_note", {"tag_id": tag_id, "note_id": self.note_id}
        )
        self.assertFalse(attached["already_attached"])
        self.assertTrue(
            self._action(
                "joplin_add_tag_to_note", {"tag_id": tag_id, "note_id": self.note_id}
            )["already_attached"]
        )
        tagged_notes = self._action(
            "joplin_list_tag_notes", {"tag_id": tag_id, "limit": 100}
        )["notes"]
        self.assertTrue(any(str(item["id"]) == self.note_id for item in tagged_notes))
        self.assertTrue(
            self._action(
                "joplin_remove_tag_from_note", {"tag_id": tag_id, "note_id": self.note_id}
            )["was_attached"]
        )
        self.assertFalse(
            self._action(
                "joplin_remove_tag_from_note", {"tag_id": tag_id, "note_id": self.note_id}
            )["was_attached"]
        )

        resources = self._action("joplin_list_resources", {"limit": 100})["resources"]
        self.assertTrue(any(str(item["id"]) == self.resource_id for item in resources))
        resource = self._action(
            "joplin_get_resource", {"resource_id": self.resource_id}
        )["resource"]
        self.assertEqual(resource["id"], self.resource_id)
        note_resources = self._action(
            "joplin_list_note_resources", {"note_id": self.note_id, "limit": 100}
        )["resources"]
        self.assertTrue(any(str(item["id"]) == self.resource_id for item in note_resources))
        resource_notes: list[dict[str, Any]] = []
        for _ in range(20):
            resource_notes = self._action(
                "joplin_list_resource_notes",
                {"resource_id": self.resource_id, "limit": 100},
            )["notes"]
            if any(str(item["id"]) == self.note_id for item in resource_notes):
                break
            time.sleep(0.25)
        self.assertTrue(any(str(item["id"]) == self.note_id for item in resource_notes))

        self.assertTrue(
            self._action("joplin_delete_tag", {"tag_id": tag_id})["permanent"]
        )
        self.assertTrue(
            self._action("joplin_delete_resource", {"resource_id": self.resource_id})[
                "permanent"
            ]
        )

    def test_04_schema_domain_size_rate_and_upstream_failures(self) -> None:
        missing_id = uuid.uuid4().hex
        self._error(
            "joplin_get_note",
            {"note_id": missing_id},
            status=422,
            code="NOTE_NOT_FOUND",
        )
        self._error(
            "joplin_list_notes", {"limit": 101}, status=422, code="INVALID_ARGUMENT"
        )
        before = self.api.get_note(self.note_id, include_deleted=True)
        self._error(
            "joplin_update_note",
            {"note_id": self.note_id},
            status=422,
            code="INVALID_ARGUMENT",
        )
        after = self.api.get_note(self.note_id, include_deleted=True)
        self.assertEqual(before["updated_time"], after["updated_time"])
        self._error(
            "joplin_create_note",
            {"title": f"jms-gpt-invalid-{self.run_id}", "parent_id": missing_id},
            status=422,
            code="NOTEBOOK_NOT_FOUND",
        )
        self._error(
            "joplin_create_notebook",
            {"title": f"jms-gpt-invalid-icon-{self.run_id}", "icon": "fas fa-book"},
            status=422,
            code="INVALID_ARGUMENT",
        )

        response_process, response_url = self._spawn_server(
            [
                "--gpt-actions-max-response-chars",
                "256",
                "--gpt-actions-rate-limit",
                "1000",
                "--retry-timeout",
                "0",
                "--quiet",
            ]
        )
        try:
            self._wait_until_ready(response_process, response_url)
            self._error(
                "joplin_get_note",
                {"note_id": self.note_id},
                status=502,
                code="RESULT_TOO_LARGE",
                base_url=response_url,
            )
            status, body, _ = self._request(
                "joplin_update_note",
                {
                    "note_id": self.note_id,
                    "body": f"large-response write completed {self.run_id}",
                },
                base_url=response_url,
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["result"]["result_omitted"])
            self.assertIn(self.run_id, self.api.get_note(self.note_id)["body"])
        finally:
            self._stop_process(response_process)

        rate_process, rate_url = self._spawn_server(
            ["--gpt-actions-rate-limit", "1", "--retry-timeout", "0", "--quiet"]
        )
        try:
            self._wait_until_ready(rate_process, rate_url)
            self.assertEqual(
                self._request(
                    "joplin_list_notes", {"limit": 1}, base_url=rate_url
                )[0],
                200,
            )
            status, body, headers = self._request(
                "joplin_list_notes", {"limit": 1}, base_url=rate_url
            )
            self.assertEqual(status, 429)
            self.assertEqual(body["error"]["code"], "RATE_LIMITED")
            self.assertIn("Retry-After", headers)
        finally:
            self._stop_process(rate_process)

        unavailable_process, unavailable_url = self._spawn_server(
            ["--gpt-actions-rate-limit", "1000", "--retry-timeout", "0", "--quiet"],
            base_url="http://127.0.0.1:1",
        )
        try:
            self._wait_until_ready(unavailable_process, unavailable_url)
            error = self._error(
                "joplin_list_notes",
                {"limit": 1},
                status=503,
                code="API_UNAVAILABLE",
                base_url=unavailable_url,
            )
            self.assertTrue(error["retryable"])
            self.assertIsNone(unavailable_process.poll())
        finally:
            self._stop_process(unavailable_process)

        bad_token_file = Path(self.auth_tmp.name) / "wrong-joplin-token"
        bad_joplin_token = secrets.token_urlsafe(48)
        self.sensitive_values.add(bad_joplin_token)
        self._write_secret(bad_token_file, bad_joplin_token)
        auth_process, auth_url = self._spawn_server(
            ["--gpt-actions-rate-limit", "1000", "--retry-timeout", "0", "--quiet"],
            token_file=bad_token_file,
        )
        try:
            self._wait_until_ready(auth_process, auth_url)
            error = self._error(
                "joplin_list_notes",
                {"limit": 1},
                status=502,
                code="UPSTREAM_AUTH_FAILED",
                base_url=auth_url,
            )
            self.assertFalse(error["retryable"])
        finally:
            self._stop_process(auth_process)

    def test_05_equal_credentials_and_current_version_fail_safely(self) -> None:
        common = [
            sys.executable,
            "-m",
            "joplin_md_sync",
            "mcp",
            "serve",
            "--base-url",
            self.api.base_url,
            "--mcp-port",
            str(_free_port()),
            "--gpt-actions",
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        cases = (
            [
                *common,
                "--token-file",
                str(TOKEN_FILE),
                "--gpt-actions-token-file",
                str(TOKEN_FILE),
            ],
            [
                *common,
                "--token-file",
                str(TOKEN_FILE),
                "--auth-token-file",
                str(self.actions_token_file),
                "--gpt-actions-token-file",
                str(self.actions_token_file),
            ],
        )
        for command in cases:
            completed = subprocess.run(
                command,
                cwd=REPO,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 7)
            output = completed.stdout + completed.stderr
            self.assertNotIn(self.joplin_token, output)
            self.assertNotIn(self.actions_token, output)

        version = subprocess.run(
            [sys.executable, "-m", "joplin_md_sync", "version", "--json"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(json.loads(version.stdout)["tool_version"], "1.4.1")

    def test_99_every_exposed_action_was_exercised(self) -> None:
        expected = {tool.name for tool in registry_for_export().exposed}
        self.assertEqual(self.called_tools, expected)


if __name__ == "__main__":
    unittest.main()
