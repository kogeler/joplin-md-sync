"""MCP Streamable HTTP lifecycle, note tools, resilience, and authorization."""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.api import JoplinClient
from joplin_md_sync.errors import ApiError
from joplin_md_sync.mcp_server import (
    BearerTokenSource,
    McpDispatcher,
    McpHttpServer,
)
from joplin_md_sync.mcp_service import JoplinMcpService
from tests.helpers import TOKEN, WorkspaceTestCase, run_cli

EXPECTED_MCP_TOOLS = {
    "joplin_list_notebooks",
    "joplin_get_notebook",
    "joplin_create_notebook",
    "joplin_update_notebook",
    "joplin_delete_notebook",
    "joplin_restore_notebook",
    "joplin_list_notebook_notes",
    "joplin_list_notes",
    "joplin_get_note",
    "joplin_create_note",
    "joplin_update_note",
    "joplin_delete_note",
    "joplin_restore_note",
    "joplin_search_notes",
    "joplin_list_tags",
    "joplin_get_tag",
    "joplin_create_tag",
    "joplin_update_tag",
    "joplin_delete_tag",
    "joplin_list_tag_notes",
    "joplin_add_tag_to_note",
    "joplin_remove_tag_from_note",
    "joplin_list_resources",
    "joplin_get_resource",
    "joplin_read_resource",
    "joplin_create_resource",
    "joplin_update_resource",
    "joplin_delete_resource",
    "joplin_list_note_resources",
    "joplin_list_resource_notes",
}


class McpHttpTest(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.available = True

        def client_factory() -> JoplinClient:
            if not self.available:
                raise ApiError("Joplin is temporarily unavailable")
            return JoplinClient(
                self.server.base_url, TOKEN, timeout=0.2, retries=1, backoff_base=0.001
            )

        service = JoplinMcpService(
            client_factory, availability_timeout=0, retry_delay=0.001
        )
        self.dispatcher = McpDispatcher(service)
        self.httpd = McpHttpServer(("127.0.0.1", 0), self.dispatcher)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_mcp)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"

    def _stop_mcp(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        url: str | None = None,
    ) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(
            url or self.url, data=data, method=method, headers=request_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return response.status, body, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body = json.loads(raw) if raw else None
            return exc.code, body, dict(exc.headers.items())

    def call_tool(self, request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        status, body, _ = self.request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        self.assertEqual(status, 200)
        assert body is not None
        return body["result"]

    def test_lifecycle_and_transport_contract(self) -> None:
        status, body, _ = self.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        self.assertEqual(status, 200)
        assert body is not None
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", body["result"]["capabilities"])

        status, body, _ = self.request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertEqual((status, body), (202, None))

        status, body, _ = self.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(status, 200)
        assert body is not None
        tools = body["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, EXPECTED_MCP_TOOLS)
        self.assertTrue(all(tool["inputSchema"]["type"] == "object" for tool in tools))
        by_name = {tool["name"]: tool for tool in tools}
        for name in (
            "joplin_delete_note",
            "joplin_delete_notebook",
            "joplin_delete_tag",
            "joplin_delete_resource",
        ):
            self.assertTrue(by_name[name]["annotations"]["destructiveHint"])
        self.assertFalse(by_name["joplin_create_note"]["annotations"]["destructiveHint"])

        status, _, headers = self.request(method="GET")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")

    def test_note_crud_metadata_tags_and_search(self) -> None:
        listed = self.call_tool(1, "joplin_list_notes", {"limit": 10})
        self.assertFalse(listed["isError"])
        self.assertEqual(listed["structuredContent"]["count"], 2)

        read = self.call_tool(2, "joplin_get_note", {"note_id": self.note_k8s})
        note = read["structuredContent"]["note"]
        self.assertEqual(note["body"], "# Cluster\n\nline one\n")
        self.assertEqual(note["metadata"]["tags"], ["homelab"])
        self.assertEqual(note["metadata"]["notebook"]["title"], "Work")

        created = self.call_tool(
            3,
            "joplin_create_note",
            {
                "title": "MCP created note",
                "body": "searchable sentinel",
                "parent_id": self.folder_work,
                "tags": ["Automation", "homelab"],
                "author": "Agent",
            },
        )
        self.assertFalse(created["isError"])
        created_note = created["structuredContent"]["note"]
        note_id = created_note["id"]
        self.assertEqual(created_note["metadata"]["tags"], ["automation", "homelab"])
        self.assertEqual(created_note["metadata"]["author"], "Agent")

        updated = self.call_tool(
            4,
            "joplin_update_note",
            {
                "note_id": note_id,
                "title": "MCP updated note",
                "body": "updated searchable sentinel",
                "tags": ["updated"],
                "is_todo": True,
            },
        )
        updated_note = updated["structuredContent"]["note"]
        self.assertEqual(updated_note["metadata"]["tags"], ["updated"])
        self.assertEqual(updated_note["metadata"]["is_todo"], 1)

        searched = self.call_tool(
            5, "joplin_search_notes", {"query": "searchable sentinel", "limit": 10}
        )
        results = searched["structuredContent"]["notes"]
        self.assertEqual([item["id"] for item in results], [note_id])

        deleted = self.call_tool(6, "joplin_delete_note", {"note_id": note_id})
        self.assertTrue(deleted["structuredContent"]["trashed"])
        self.assertGreater(self.server.store.notes[note_id]["deleted_time"], 0)

        restored = self.call_tool(7, "joplin_restore_note", {"note_id": note_id})
        self.assertFalse(restored["structuredContent"]["already_active"])
        self.assertEqual(self.server.store.notes[note_id]["deleted_time"], 0)

    def test_notebook_crud_nesting_notes_trash_and_restore(self) -> None:
        created = self.call_tool(
            1,
            "joplin_create_notebook",
            {
                "title": "MCP notebook",
                "icon": json.dumps(
                    {"type": 3, "emoji": "", "name": "fas fa-book", "dataUrl": ""}
                ),
            },
        )["structuredContent"]["notebook"]
        notebook_id = created["id"]
        self.assertEqual(created["title"], "MCP notebook")
        self.assertEqual(json.loads(created["icon"])["name"], "fas fa-book")

        read = self.call_tool(
            2, "joplin_get_notebook", {"notebook_id": notebook_id}
        )["structuredContent"]["notebook"]
        self.assertEqual(read["id"], notebook_id)

        updated = self.call_tool(
            3,
            "joplin_update_notebook",
            {
                "notebook_id": notebook_id,
                "title": "MCP notebook renamed",
                "parent_id": self.folder_work,
            },
        )["structuredContent"]["notebook"]
        self.assertEqual(updated["title"], "MCP notebook renamed")
        self.assertEqual(updated["parent_id"], self.folder_work)

        note = self.call_tool(
            4,
            "joplin_create_note",
            {"title": "Notebook child", "parent_id": notebook_id},
        )["structuredContent"]["note"]
        listed = self.call_tool(
            5, "joplin_list_notebook_notes", {"notebook_id": notebook_id}
        )["structuredContent"]
        self.assertEqual([item["id"] for item in listed["notes"]], [note["id"]])

        disposable = self.call_tool(
            6, "joplin_create_notebook", {"title": "Disposable MCP notebook"}
        )["structuredContent"]["notebook"]
        disposable_id = disposable["id"]
        deleted = self.call_tool(
            7, "joplin_delete_notebook", {"notebook_id": disposable_id}
        )["structuredContent"]
        self.assertFalse(deleted["already_trashed"])
        self.assertGreater(self.server.store.folders[disposable_id]["deleted_time"], 0)
        deleted_list = self.call_tool(
            8, "joplin_list_notebooks", {"include_deleted": True}
        )["structuredContent"]["notebooks"]
        self.assertTrue(any(item["id"] == disposable_id for item in deleted_list))
        restored = self.call_tool(
            9, "joplin_restore_notebook", {"notebook_id": disposable_id}
        )["structuredContent"]
        self.assertFalse(restored["already_active"])
        self.assertEqual(self.server.store.folders[disposable_id]["deleted_time"], 0)

    def test_tag_crud_listing_and_note_relations(self) -> None:
        created = self.call_tool(
            1, "joplin_create_tag", {"title": "MCP Tag"}
        )["structuredContent"]
        tag_id = created["tag"]["id"]
        self.assertTrue(created["created"])

        listed = self.call_tool(2, "joplin_list_tags", {})["structuredContent"]["tags"]
        self.assertTrue(any(item["id"] == tag_id for item in listed))
        read = self.call_tool(3, "joplin_get_tag", {"tag_id": tag_id})[
            "structuredContent"
        ]["tag"]
        self.assertEqual(read["title"], "mcp tag")

        renamed = self.call_tool(
            4, "joplin_update_tag", {"tag_id": tag_id, "title": "Renamed MCP Tag"}
        )["structuredContent"]["tag"]
        self.assertEqual(renamed["title"], "renamed mcp tag")

        attached = self.call_tool(
            5,
            "joplin_add_tag_to_note",
            {"tag_id": tag_id, "note_id": self.note_k8s},
        )["structuredContent"]
        self.assertFalse(attached["already_attached"])
        tag_notes = self.call_tool(
            6, "joplin_list_tag_notes", {"tag_id": tag_id}
        )["structuredContent"]["notes"]
        self.assertEqual([item["id"] for item in tag_notes], [self.note_k8s])

        removed = self.call_tool(
            7,
            "joplin_remove_tag_from_note",
            {"tag_id": tag_id, "note_id": self.note_k8s},
        )["structuredContent"]
        self.assertTrue(removed["was_attached"])
        deleted = self.call_tool(
            8, "joplin_delete_tag", {"tag_id": tag_id}
        )["structuredContent"]
        self.assertTrue(deleted["permanent"])
        self.assertNotIn(tag_id, self.server.store.tags)

    def test_resource_crud_content_and_note_relations(self) -> None:
        initial_data = b"resource payload\x00"
        created = self.call_tool(
            1,
            "joplin_create_resource",
            {
                "filename": "payload.bin",
                "mime": "application/octet-stream",
                "title": "MCP payload",
                "content_base64": base64.b64encode(initial_data).decode("ascii"),
            },
        )["structuredContent"]["resource"]
        resource_id = created["id"]
        self.assertEqual(created["size"], len(initial_data))

        read = self.call_tool(
            2, "joplin_read_resource", {"resource_id": resource_id}
        )["structuredContent"]
        self.assertEqual(base64.b64decode(read["content_base64"]), initial_data)

        replacement = b"updated resource payload"
        updated = self.call_tool(
            3,
            "joplin_update_resource",
            {
                "resource_id": resource_id,
                "title": "Updated payload",
                "content_base64": base64.b64encode(replacement).decode("ascii"),
            },
        )["structuredContent"]["resource"]
        self.assertEqual(updated["title"], "Updated payload")
        self.assertEqual(self.server.store.resource_files[resource_id], replacement)

        self.call_tool(
            4,
            "joplin_update_note",
            {"note_id": self.note_k8s, "body": f"![payload](:/{resource_id})"},
        )
        note_resources = self.call_tool(
            5, "joplin_list_note_resources", {"note_id": self.note_k8s}
        )["structuredContent"]["resources"]
        self.assertEqual([item["id"] for item in note_resources], [resource_id])
        resource_notes = self.call_tool(
            6, "joplin_list_resource_notes", {"resource_id": resource_id}
        )["structuredContent"]["notes"]
        self.assertEqual([item["id"] for item in resource_notes], [self.note_k8s])

        resources = self.call_tool(7, "joplin_list_resources", {})[
            "structuredContent"
        ]["resources"]
        self.assertTrue(any(item["id"] == resource_id for item in resources))
        deleted = self.call_tool(
            8, "joplin_delete_resource", {"resource_id": resource_id}
        )["structuredContent"]
        self.assertTrue(deleted["permanent"])

    def test_create_note_from_html_and_with_binary_attachments(self) -> None:
        html_note = self.call_tool(
            1,
            "joplin_create_note",
            {
                "title": "HTML MCP note",
                "body_html": "<p>HTML <strong>content</strong></p>",
                "parent_id": self.folder_work,
            },
        )["structuredContent"]["note"]
        self.assertIn("HTML", html_note["body"])

        image_data = b"\x89PNG\r\n\x1a\nsmall-test-image"
        created = self.call_tool(
            2,
            "joplin_create_note",
            {
                "title": "MCP note with attachment",
                "body": "Attached below",
                "parent_id": self.folder_work,
                "attachments": [
                    {
                        "filename": "small.png",
                        "mime": "image/png",
                        "alt_text": "small image",
                        "content_base64": base64.b64encode(image_data).decode("ascii"),
                    }
                ],
            },
        )["structuredContent"]
        note = created["note"]
        resource = created["created_resources"][0]
        self.assertIn(f"![small image](:/{resource['id']})", note["body"])
        self.assertEqual(note["metadata"]["resources"][0]["id"], resource["id"])
        self.assertEqual(self.server.store.resource_files[resource["id"]], image_data)

    def test_joplin_outage_is_retryable_tool_error_and_server_recovers(self) -> None:
        self.available = False
        failed = self.call_tool(1, "joplin_list_notes", {})
        self.assertTrue(failed["isError"])
        self.assertEqual(failed["structuredContent"]["error"]["code"], "API_UNAVAILABLE")
        self.assertTrue(failed["structuredContent"]["error"]["retryable"])

        self.available = True
        recovered = self.call_tool(2, "joplin_list_notes", {})
        self.assertFalse(recovered["isError"])
        self.assertEqual(recovered["structuredContent"]["count"], 2)

    def test_create_note_can_create_its_notebook_and_validates_parent(self) -> None:
        created = self.call_tool(
            1,
            "joplin_create_note",
            {
                "title": "note with automatic notebook",
                "notebook_title": "MCP test notebook",
            },
        )
        note = created["structuredContent"]["note"]
        self.assertEqual(note["metadata"]["notebook"]["title"], "MCP test notebook")

        failed = self.call_tool(
            2,
            "joplin_create_note",
            {"title": "invalid parent", "parent_id": "0" * 32},
        )
        self.assertTrue(failed["isError"])
        self.assertEqual(
            failed["structuredContent"]["error"]["code"], "NOTEBOOK_NOT_FOUND"
        )

    def test_entity_and_content_validation_errors_are_structured(self) -> None:
        invalid_icon = self.call_tool(
            0,
            "joplin_create_notebook",
            {"title": "invalid icon", "icon": "fas fa-book"},
        )
        self.assertTrue(invalid_icon["isError"])
        self.assertEqual(
            invalid_icon["structuredContent"]["error"]["code"], "INVALID_ARGUMENT"
        )

        both_bodies = self.call_tool(
            1,
            "joplin_create_note",
            {"title": "invalid bodies", "body": "Markdown", "body_html": "<p>HTML</p>"},
        )
        self.assertTrue(both_bodies["isError"])
        self.assertEqual(
            both_bodies["structuredContent"]["error"]["code"], "INVALID_ARGUMENT"
        )

        invalid_base64 = self.call_tool(
            2,
            "joplin_create_resource",
            {
                "filename": "bad.bin",
                "mime": "application/octet-stream",
                "content_base64": "not-base64!",
            },
        )
        self.assertTrue(invalid_base64["isError"])
        self.assertEqual(
            invalid_base64["structuredContent"]["error"]["code"], "INVALID_ARGUMENT"
        )

        missing_id = "0" * 32
        for request_id, tool, key, code in (
            (3, "joplin_get_notebook", "notebook_id", "NOTEBOOK_NOT_FOUND"),
            (4, "joplin_get_tag", "tag_id", "TAG_NOT_FOUND"),
            (5, "joplin_get_resource", "resource_id", "RESOURCE_NOT_FOUND"),
        ):
            failed = self.call_tool(request_id, tool, {key: missing_id})
            self.assertTrue(failed["isError"])
            self.assertEqual(failed["structuredContent"]["error"]["code"], code)

    def test_http_validation_and_origin_protection(self) -> None:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        status, _, _ = self.request(payload, headers={"Accept": "application/json"})
        self.assertEqual(status, 406)
        status, _, _ = self.request(payload, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, body, _ = self.request(
            payload, headers={"MCP-Protocol-Version": "1900-01-01"}
        )
        self.assertEqual(status, 400)
        assert body is not None
        self.assertIn("Unsupported", body["error"]["message"])

        deeply_nested = b"[" * 10_000 + b"]" * 10_000
        request = urllib.request.Request(
            self.url,
            data=deeply_nested,
            method="POST",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.read()

        malformed = socket.create_connection(self.httpd.server_address, timeout=2)
        try:
            malformed.sendall(b"GET http://[ HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b" 400 ", malformed.recv(1024).split(b"\r\n", 1)[0])
        finally:
            malformed.close()
        self.assertEqual(self.request(payload)[0], 200)

    def test_bearer_authentication_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-auth-") as tmp:
            token_file = Path(tmp) / "token"
            first_token = "Zmlyc3Qtc2VjcmV0LWZpcnN0LXNlY3JldC0xMjM0NTY3ODkwMTI"
            second_token = "c2Vjb25kLXNlY3JldC1zZWNvbmQtc2VjcmV0LTEyMzQ1Njc4OTA"
            token_file.write_text(first_token + "\n", encoding="utf-8")
            if os.name == "posix":
                token_file.chmod(0o600)
            auth_httpd = McpHttpServer(
                ("127.0.0.1", 0),
                self.dispatcher,
                token_source=BearerTokenSource(token_file),
            )
            thread = threading.Thread(target=auth_httpd.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{auth_httpd.server_address[1]}/mcp"
            try:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
                self.assertEqual(self.request(payload, url=url)[0], 401)
                headers = {"Authorization": f"Bearer {first_token}"}
                self.assertEqual(self.request(payload, url=url, headers=headers)[0], 200)
                token_file.write_text(second_token + "\n", encoding="utf-8")
                self.assertEqual(self.request(payload, url=url, headers=headers)[0], 401)
                headers = {"Authorization": f"Bearer {second_token}"}
                self.assertEqual(self.request(payload, url=url, headers=headers)[0], 200)

                raw = json.dumps(payload).encode("utf-8")

                def raw_request(authorizations: tuple[str, ...]) -> int:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", auth_httpd.server_address[1], timeout=3
                    )
                    try:
                        connection.putrequest("POST", "/mcp")
                        connection.putheader(
                            "Accept", "application/json, text/event-stream"
                        )
                        connection.putheader("Content-Type", "application/json")
                        connection.putheader("Content-Length", str(len(raw)))
                        for authorization in authorizations:
                            connection.putheader("Authorization", authorization)
                        connection.endheaders(raw)
                        response = connection.getresponse()
                        response.read()
                        return response.status
                    finally:
                        connection.close()

                valid = f"Bearer {second_token}"
                self.assertEqual(raw_request((valid, valid)), 401)
                self.assertEqual(raw_request((valid, "Bearer wrong-token")), 401)
                self.assertEqual(raw_request(("Bearer \x80",)), 401)
                self.assertEqual(self.request(payload, url=url, headers=headers)[0], 200)

                for authorization, expected_status in ((None, 401), (valid, 405)):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", auth_httpd.server_address[1], timeout=3
                    )
                    try:
                        method_headers = (
                            {} if authorization is None else {"Authorization": authorization}
                        )
                        connection.request("HEAD", "/mcp", headers=method_headers)
                        response = connection.getresponse()
                        response.read()
                        self.assertEqual(response.status, expected_status)
                    finally:
                        connection.close()
            finally:
                auth_httpd.shutdown()
                auth_httpd.server_close()
                thread.join(timeout=2)

    def test_pre_auth_connection_count_is_bounded(self) -> None:
        limited = McpHttpServer(
            ("127.0.0.1", 0),
            self.dispatcher,
            max_http_connections=1,
            connection_timeout=1,
        )
        thread = threading.Thread(target=limited.serve_forever, daemon=True)
        thread.start()
        stalled = socket.create_connection(limited.server_address, timeout=2)
        stalled.sendall(b"GET /mcp HTTP/1.1\r\nHost: localhost\r\n")
        deadline = time.monotonic() + 1
        while limited._request_slots.acquire(blocking=False):
            limited._request_slots.release()
            if time.monotonic() >= deadline:
                self.fail("stalled request did not occupy the bounded handler slot")
            time.sleep(0.01)

        rejected = socket.create_connection(limited.server_address, timeout=2)
        try:
            rejected.sendall(b"GET /mcp HTTP/1.1\r\nHost: localhost\r\n\r\n")
            try:
                self.assertEqual(rejected.recv(1024), b"")
            except ConnectionError:
                pass
        finally:
            rejected.close()
            stalled.close()

        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            url = f"http://127.0.0.1:{limited.server_address[1]}/mcp"
            deadline = time.monotonic() + 2
            while True:
                try:
                    status = self.request(payload, url=url)[0]
                except (ConnectionError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
                    continue
                self.assertEqual(status, 200)
                break
        finally:
            limited.shutdown()
            limited.server_close()
            thread.join(timeout=2)


class McpCliSafetyTest(WorkspaceTestCase):
    seed_remote = False

    def test_non_loopback_bind_requires_flag_and_auth(self) -> None:
        result = run_cli("mcp", "serve", "--host", "0.0.0.0", "--mcp-port", "8765")
        self.assertEqual(result.exit_code, 7)
        self.assertIn("--allow-remote-mcp", result.stdout)

        result = run_cli(
            "mcp", "serve", "--host", "0.0.0.0", "--mcp-port", "8765",
            "--allow-remote-mcp",
        )
        self.assertEqual(result.exit_code, 7)
        self.assertIn("--auth-token-file", result.stdout)

    def test_capabilities_advertise_mcp(self) -> None:
        result = run_cli("capabilities", "--json")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("mcp serve", result.json["commands"])
        self.assertTrue(result.json["features"]["mcp_streamable_http"])


if __name__ == "__main__":
    import unittest

    unittest.main()
