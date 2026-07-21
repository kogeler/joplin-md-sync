"""GPT Actions HTTP chain through the shared executor and fake Joplin backend."""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.api import JoplinClient
from joplin_md_sync.gpt_actions import (
    ActionsConfig,
    ActionsTokenSource,
    GptActionsTransport,
)
from joplin_md_sync.mcp_server import (
    BearerTokenSource,
    McpDispatcher,
    McpHttpServer,
)
from joplin_md_sync.mcp_service import JoplinMcpService
from tests.helpers import TOKEN, WorkspaceTestCase

ACTIONS_TOKEN = "actions-token-0123456789abcdef-0123456789ab"


class GptActionsHttpTest(WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._auth_tmp = tempfile.TemporaryDirectory(prefix="gpt-actions-auth-")
        self.addCleanup(self._auth_tmp.cleanup)
        self.token_file = Path(self._auth_tmp.name) / "token"
        self._write_token(ACTIONS_TOKEN)

        service = JoplinMcpService(
            lambda: JoplinClient(
                self.server.base_url,
                TOKEN,
                timeout=0.2,
                retries=1,
                backoff_base=0.001,
            ),
            availability_timeout=0,
        )
        dispatcher = McpDispatcher(service)
        transport = GptActionsTransport(
            dispatcher.registry,
            dispatcher.executor,
            ActionsTokenSource(self.token_file),
            config=ActionsConfig(rate_limit_per_minute=1_000),
        )
        self.httpd = McpHttpServer(
            ("127.0.0.1", 0), dispatcher, actions_transport=transport
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_actions)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def _write_token(self, token: str) -> None:
        self.token_file.write_text(token + "\n", encoding="utf-8")
        if os.name == "posix":
            self.token_file.chmod(0o600)

    def _stop_actions(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        payload: object | None = None,
        *,
        raw: bytes | None = None,
        method: str = "POST",
        token: str | None = ACTIONS_TOKEN,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        data = raw
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return (
                    response.status,
                    json.loads(response.read()),
                    dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers.items())

    def tool_path(self, name: str) -> str:
        return f"/api/gpt/v1/tools/{name}"

    def test_read_action_uses_real_service_chain(self) -> None:
        status, body, headers = self.request(
            self.tool_path("joplin_list_notes"), {"limit": 10}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["result"]["count"], 2)
        self.assertEqual(body["request_id"], headers["X-Request-ID"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_authentication_is_uniform_independent_and_rotatable(self) -> None:
        path = self.tool_path("joplin_list_notes")
        responses = []
        for token in (None, "wrong-token", TOKEN):
            status, body, headers = self.request(path, {}, token=token)
            self.assertEqual(status, 401)
            self.assertEqual(body["error"]["code"], "UNAUTHORIZED")
            self.assertFalse(body["error"]["retryable"])
            self.assertIn("joplin-md-sync-gpt-actions", headers["WWW-Authenticate"])
            responses.append(body["error"])
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[1], responses[2])

        self._write_token("rotated-actions-token-0123456789abcdef-0123")
        self.assertEqual(self.request(path, {}, token=ACTIONS_TOKEN)[0], 401)
        self.assertEqual(
            self.request(path, {}, token="rotated-actions-token-0123456789abcdef-0123")[0],
            200,
        )

    def test_malformed_and_duplicate_authorization_fail_closed(self) -> None:
        path = self.tool_path("joplin_list_notes")

        def raw_request(authorizations: tuple[str, ...]) -> int:
            payload = b"{}"
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.httpd.server_address[1], timeout=3
            )
            try:
                connection.putrequest("POST", path)
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(len(payload)))
                for authorization in authorizations:
                    connection.putheader("Authorization", authorization)
                connection.endheaders(payload)
                response = connection.getresponse()
                response.read()
                return response.status
            finally:
                connection.close()

        valid = f"Bearer {ACTIONS_TOKEN}"
        self.assertEqual(raw_request((valid, valid)), 401)
        self.assertEqual(raw_request((valid, "Bearer wrong-token")), 401)
        self.assertEqual(raw_request(("Bearer \x80",)), 401)
        self.assertEqual(self.request(path, {})[0], 200)

    def test_route_is_fail_closed_and_auth_hides_route_details(self) -> None:
        disabled = self.tool_path("joplin_read_resource")
        unknown = self.tool_path("definitely_absent")
        self.assertEqual(self.request(disabled, {}, token=None)[0], 401)
        self.assertEqual(self.request(unknown, {}, token=None)[0], 401)
        self.assertEqual(self.request(disabled, {})[0], 404)
        self.assertEqual(self.request(unknown, {})[0], 404)

    def test_http_and_schema_validation_precede_side_effects(self) -> None:
        path = self.tool_path("joplin_create_tag")
        before = len(self.server.store.tags)
        self.assertEqual(self.request(path, raw=b"{", token=ACTIONS_TOKEN)[0], 400)
        deeply_nested = b"[" * 10_000 + b"]" * 10_000
        self.assertEqual(
            self.request(path, raw=deeply_nested, token=ACTIONS_TOKEN)[0], 400
        )
        self.assertEqual(
            self.request(path, {}, content_type="text/plain")[0], 415
        )
        status, body, _ = self.request(path, {})
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(len(self.server.store.tags), before)

    def test_known_route_methods_and_local_health(self) -> None:
        path = self.tool_path("joplin_list_notes")
        status, body, headers = self.request(path, method="GET")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")
        self.assertEqual(body["error"]["code"], "METHOD_NOT_ALLOWED")
        head = urllib.request.Request(
            f"{self.base_url}{path}",
            method="HEAD",
            headers={"Authorization": f"Bearer {ACTIONS_TOKEN}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(head, timeout=3)
        self.assertEqual(caught.exception.code, 405)
        self.assertEqual(caught.exception.headers["Allow"], "POST")
        self.assertEqual(caught.exception.read(), b"")
        for health_path in ("/healthz", "/readyz"):
            status, body, _ = self.request(health_path, method="GET", token=None)
            self.assertEqual((status, body), (200, {"ok": True}))

    def test_request_limit_and_result_limit_semantics(self) -> None:
        dispatcher = self.httpd.dispatcher
        limited = GptActionsTransport(
            dispatcher.registry,
            dispatcher.executor,
            ActionsTokenSource(self.token_file),
            config=ActionsConfig(
                max_request_bytes=100,
                max_response_chars=256,
                rate_limit_per_minute=1_000,
            ),
        )
        self.httpd.actions_transport = limited
        status, body, _ = self.request(
            self.tool_path("joplin_search_notes"),
            {"query": "x" * 200},
        )
        self.assertEqual(status, 413)
        self.assertEqual(body["error"]["code"], "REQUEST_TOO_LARGE")

        status, body, _ = self.request(self.tool_path("joplin_list_notes"), {})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "RESULT_TOO_LARGE")

        status, body, _ = self.request(
            self.tool_path("joplin_create_tag"), {"title": "Result omitted tag"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertTrue(body["result"]["result_omitted"])
        self.assertTrue(
            any(tag["title"] == "result omitted tag" for tag in self.server.store.tags.values())
        )

    def test_concurrency_capacity_and_authenticated_rate_limit(self) -> None:
        dispatcher = self.httpd.dispatcher
        limited = GptActionsTransport(
            dispatcher.registry,
            dispatcher.executor,
            ActionsTokenSource(self.token_file),
            config=ActionsConfig(max_concurrency=1, rate_limit_per_minute=1),
        )
        self.httpd.actions_transport = limited
        self.assertTrue(limited._capacity.acquire(blocking=False))
        try:
            status, body, headers = self.request(
                self.tool_path("joplin_list_notes"), {}
            )
            self.assertEqual(status, 503)
            self.assertTrue(body["error"]["retryable"])
            self.assertEqual(headers["Retry-After"], "1")
        finally:
            limited._capacity.release()

        self.assertEqual(
            self.request(self.tool_path("definitely_absent"), {})[0], 404
        )
        status, body, headers = self.request(
            self.tool_path("joplin_list_notes"), {}
        )
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], "RATE_LIMITED")
        self.assertIn("Retry-After", headers)

    def test_mcp_actions_and_joplin_credentials_are_independent(self) -> None:
        mcp_token = "bWNwLXRva2VuLW1jcC10b2tlbi1tY3AtdG9rZW4tMTIzNDU2Nzg5"
        mcp_file = Path(self._auth_tmp.name) / "mcp-token"
        mcp_file.write_text(mcp_token + "\n", encoding="utf-8")
        if os.name == "posix":
            mcp_file.chmod(0o600)
        self.httpd.token_source = BearerTokenSource(mcp_file)
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        ).encode("utf-8")

        def mcp_request(token: str) -> int:
            request = urllib.request.Request(
                f"{self.base_url}/mcp",
                data=payload,
                method="POST",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                exc.read()
                return exc.code

        self.assertEqual(mcp_request(ACTIONS_TOKEN), 401)
        self.assertEqual(mcp_request(mcp_token), 200)
        self.assertEqual(
            self.request(
                self.tool_path("joplin_list_notes"), {}, token=mcp_token
            )[0],
            401,
        )

    def test_backend_auth_is_not_public_actions_auth_and_logs_are_redacted(self) -> None:
        bad_service = JoplinMcpService(
            lambda: JoplinClient(
                self.server.base_url,
                "wrong-joplin-token",
                timeout=0.2,
                retries=1,
                backoff_base=0.001,
            ),
            availability_timeout=0,
        )
        dispatcher = McpDispatcher(bad_service)
        self.httpd.actions_transport = GptActionsTransport(
            dispatcher.registry,
            dispatcher.executor,
            ActionsTokenSource(self.token_file),
            config=ActionsConfig(rate_limit_per_minute=1_000),
        )
        with self.assertLogs("joplin_md_sync.gpt_actions", level="INFO") as captured:
            status, body, _ = self.request(
                self.tool_path("joplin_search_notes"),
                {"query": "private-search-sentinel"},
            )
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "UPSTREAM_AUTH_FAILED")
        logs = "\n".join(captured.output)
        self.assertNotIn("private-search-sentinel", logs)
        self.assertNotIn(ACTIONS_TOKEN, logs)
        self.assertNotIn("wrong-joplin-token", logs)


if __name__ == "__main__":
    import unittest

    unittest.main()
