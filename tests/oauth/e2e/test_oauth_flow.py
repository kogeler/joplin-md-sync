"""Required OAuth E2E: real process/TLS/SDK/storage/executor, mocked external peers."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from joplin_md_sync.mcp_server import MCP_PROTOCOL_VERSION
from tests.fake_joplin_server import FakeJoplinServer
from tests.helpers import TOKEN, run_cli
from tests.oauth.e2e.peers import HttpsProxy, MockGitHubPeer
from tests.oauth.helpers import OAuthArguments, child_environment, protected_file
from tests.oauth.integration.test_provider_flow import authorize, exchange

REPO = Path(__file__).resolve().parents[3]
LOCAL_HOSTS = {None, "127.0.0.1", "::1", "localhost", b"127.0.0.1", b"::1", b"localhost"}
pytestmark = pytest.mark.oauth_e2e("core")


class RunningApplication:
    def __init__(self, directory: Path, *, backend=None, joplin_token=TOKEN):
        self.directory = directory
        self.backend = backend if backend is not None else FakeJoplinServer(token=TOKEN).start()
        self.owns_backend = backend is None
        self.github = MockGitHubPeer()
        self.port = 0
        self.listener_file = directory / "listener-port"
        self.proxy = HttpsProxy(self.port, directory)
        self.mcp_token = secrets.token_urlsafe(32)
        self.actions_token = secrets.token_urlsafe(32)
        self.joplin_token_file = protected_file(directory / "joplin-token", joplin_token)
        self.mcp_token_file = protected_file(directory / "mcp-token", self.mcp_token)
        self.actions_token_file = protected_file(directory / "actions-token", self.actions_token)
        self.oauth = OAuthArguments(directory, self.proxy.origin)
        self.clock = protected_file(directory / "clock-offset", "0")
        self.log_canaries = {
            joplin_token,
            self.mcp_token,
            self.actions_token,
            "fixture-secret",
            self.oauth.signing_path.read_text().strip(),
            "created fixture body",
            "updated fixture body",
        }
        self.process = None
        self.log = (directory / "server.log").open("ab")
        self.client = httpx2.Client(
            base_url=self.proxy.origin,
            verify=self.proxy.trust,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            event_hooks={"response": [self.track_response]},
        )

    def track_response(self, response):
        response.read()
        location = response.headers.get("location", "")
        self.log_canaries.update(parse_qs(urlsplit(location).query).get("code", []))
        if response.request.url.path == "/token" and response.status_code == 200:
            self.log_canaries.update(
                value
                for key, value in response.json().items()
                if key in {"access_token", "refresh_token"} and isinstance(value, str)
            )

    def advance(self, seconds):
        offset = float(self.clock.read_text()) + seconds
        self.clock.write_text(str(offset))
        self.github.github.clock_offset = offset

    def start(self, *, oauth=True, extra_args=()):
        self.listener_file.unlink(missing_ok=True)
        command = [
            sys.executable,
            "-m",
            "tests.oauth.e2e.server_runner",
            "--github-url",
            self.github.url,
            "--clock-file",
            str(self.clock),
            "--listener-file",
            str(self.listener_file),
            "--",
            "mcp",
            "serve",
            "--token-file",
            str(self.joplin_token_file),
            "--auth-token-file",
            str(self.mcp_token_file),
            "--gpt-actions",
            "--gpt-actions-token-file",
            str(self.actions_token_file),
            "--mcp-port",
            "8765",
            "--base-url",
            self.backend.base_url,
            "--retry-timeout",
            "0",
            "--timeout",
            "1",
            *extra_args,
        ]
        if oauth:
            command.extend(self.oauth.argv())
        self.process = subprocess.Popen(
            command, cwd=REPO, env=child_environment(), stdout=self.log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 20
        with httpx2.Client(trust_env=False, timeout=0.2) as probe:
            while True:
                assert self.process.poll() is None, (
                    "Server exited before readiness; see fixture server.log"
                )
                try:
                    if not self.listener_file.exists():
                        assert time.monotonic() < deadline, (
                            "HTTP listener startup deadline exceeded"
                        )
                        time.sleep(0.02)
                        continue
                    self.port = int(self.listener_file.read_text())
                    self.proxy.backend_port = self.port
                    if probe.get(f"http://127.0.0.1:{self.port}/healthz").status_code == 200:
                        # Readiness must belong to this fixture's unique credential.
                        response = probe.post(
                            f"http://127.0.0.1:{self.port}/mcp",
                            json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                            headers={
                                "Authorization": "Bearer " + self.mcp_token,
                                "Accept": "application/json, text/event-stream",
                                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                            },
                        )
                        if response.status_code == 200 and response.json().get("result") == {}:
                            return
                except httpx2.RequestError:
                    pass
                assert time.monotonic() < deadline, "Server startup deadline exceeded"
                time.sleep(0.02)

    def stop(self):
        if self.process is not None:
            if os.name == "nt":
                # The venv redirector can launch a child interpreter on Windows.
                # TerminateProcess on the redirector alone leaves the server,
                # its listener and its exclusive state lock running.
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None

    def close(self):
        self.stop()
        self.client.close()
        self.proxy.close()
        self.github.close()
        if self.owns_backend:
            self.backend.stop()
        self.log.close()

    def assert_private_data_not_logged(self):
        log = (self.directory / "server.log").read_text(encoding="utf-8")
        for canary in self.log_canaries | self.github.github.issued:
            assert canary not in log, "Private OAuth or note data appeared in server logs"

    def login(self):
        challenge = self.client.post("/claude/mcp", json={})
        assert challenge.status_code == 401
        metadata = self.proxy.origin + "/.well-known/oauth-protected-resource/claude/mcp"
        assert f'resource_metadata="{metadata}"' in challenge.headers["www-authenticate"]
        discovery = self.client.get("/.well-known/oauth-protected-resource/claude/mcp")
        assert discovery.status_code == 200
        assert discovery.json()["resource"] == self.proxy.origin + "/claude/mcp"
        client_id, verifier, callback = authorize(self.client, self.github)
        assert callback.status_code == 302, callback.text
        tokens = exchange(self.client, client_id, verifier, callback.headers["location"])
        assert tokens.status_code == 200, tokens.text
        issued = tokens.json()
        self.log_canaries.update((issued["access_token"], issued["refresh_token"], verifier))
        self.log_canaries.update(
            parse_qs(urlsplit(callback.headers["location"]).query).get("code", [])
        )
        return client_id, issued

    def rpc(self, token, method, params=None, *, path="/claude/mcp"):
        response = self.client.post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    def tool(self, token, name, arguments):
        result = self.rpc(token, "tools/call", {"name": name, "arguments": arguments})
        assert result["isError"] is False, result
        return result["structuredContent"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    # The child rejects external DNS itself. In-process CLI checks in this
    # process (oauth-check, oauth-reset, serve) must not reach GitHub either.
    external = []
    resolve = socket.getaddrinfo

    def local_resolve(host, *positional, **keywords):
        if host not in LOCAL_HOSTS:
            external.append(host)
            raise socket.gaierror(socket.EAI_NONAME, "Unexpected external network resolution")
        return resolve(host, *positional, **keywords)

    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
    application = RunningApplication(tmp_path)
    try:
        yield application
    finally:
        application.close()
        application.assert_private_data_not_logged()
    assert not external, f"Unexpected external network resolution: {external}"


@pytest.mark.oauth_e2e("owner")
def test_owner_crud_refresh_restart_and_revoke(app):
    # The management command exercises the same config/route builder, without
    # provisioning a directory, opening a listener or contacting GitHub.
    checked = run_cli(
        "mcp",
        "oauth-check",
        *app.oauth.argv(),
        "--auth-token-file",
        str(app.mcp_token_file),
        "--token-file",
        str(app.joplin_token_file),
        "--json",
    )
    assert checked.exit_code == 0, checked.stderr + checked.stdout
    assert not (app.directory / "state").exists()
    app.start()
    client_id, tokens = app.login()
    token = tokens["access_token"]
    app.log_canaries.update((token, tokens["refresh_token"]))
    initialized = app.rpc(
        token,
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "E2E", "version": "fixture"},
        },
    )
    assert "tools" in initialized["capabilities"]
    before = app.github.github.user_requests
    tools = app.rpc(token, "tools/list")["tools"]
    assert app.github.github.user_requests == before + 1
    legacy_tools = app.rpc(app.mcp_token, "tools/list", path="/mcp")["tools"]
    assert {tool["name"] for tool in tools} == {tool["name"] for tool in legacy_tools}
    legacy_by_name = {tool["name"]: tool for tool in legacy_tools}
    for tool in tools:
        expected = legacy_by_name[tool["name"]]
        for field in ("title", "description", "inputSchema", "outputSchema", "annotations"):
            assert tool.get(field) == expected.get(field), (tool["name"], field)
    invalid = {"name": "joplin_get_note", "arguments": {"note_id": False}}
    invalid_oauth = app.rpc(token, "tools/call", invalid)
    invalid_legacy = app.rpc(app.mcp_token, "tools/call", invalid, path="/mcp")
    assert invalid_oauth["isError"] is True
    assert invalid_oauth["structuredContent"] == invalid_legacy["structuredContent"]
    parent = app.backend.store.add_folder("OAuth E2E")
    created = app.tool(
        token,
        "joplin_create_note",
        {"parent_id": parent, "title": "OAuth E2E", "body": "created fixture body"},
    )
    note_id = created["note"]["id"]
    assert (
        app.tool(token, "joplin_get_note", {"note_id": note_id})["note"]["body"]
        == "created fixture body"
    )
    app.tool(token, "joplin_update_note", {"note_id": note_id, "body": "updated fixture body"})
    assert (
        app.tool(token, "joplin_get_note", {"note_id": note_id})["note"]["body"]
        == "updated fixture body"
    )
    app.tool(token, "joplin_delete_note", {"note_id": note_id})
    assert app.backend.store.notes[note_id]["deleted_time"] > 0
    refresh = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": tokens["refresh_token"],
    }
    refreshed = app.client.post("/token", data=refresh)
    assert refreshed.status_code == 200, refreshed.text
    app.log_canaries.update((refreshed.json()["access_token"], refreshed.json()["refresh_token"]))
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert app.github.github.refresh_count == 1
    assert app.client.post("/token", data=refresh).json()["error"] == "invalid_grant"
    app.stop()
    app.start()
    new_token = refreshed.json()["access_token"]
    assert app.rpc(new_token, "tools/list")["tools"]
    app.github.github.access.clear()
    assert (
        app.client.post(
            "/claude/mcp", json={}, headers={"Authorization": "Bearer " + new_token}
        ).status_code
        == 401
    )
    assert app.client.get("/healthz").status_code == 404
    # Losing GitHub auth does not affect the independent legacy credentials.
    assert app.rpc(app.mcp_token, "ping", path="/mcp") == {}


@pytest.mark.oauth_e2e("foreign")
def test_foreign_github_account_cannot_issue_access_or_mutate(app):
    app.github.github.user_id = 43
    app.start()
    _, _, callback = authorize(app.client, app.github)
    assert callback.status_code == 403
    assert "location" not in callback.headers
    assert app.client.post("/claude/mcp", json={}).status_code == 401
    assert not app.backend.store.notes


@pytest.mark.oauth_e2e("invalid-flow")
def test_invalid_pkce_state_code_replay_and_redirect(app):
    app.start()
    rejected = app.client.post(
        "/register",
        json={
            "redirect_uris": ["https://attacker.invalid/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert rejected.status_code == 400
    client_id, verifier, callback = authorize(app.client, app.github)
    assert callback.status_code == 302
    location = callback.headers["location"]
    wrong = exchange(app.client, client_id, "invalid-verifier-" + "x" * 43, location)
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "invalid_grant"
    tokens = exchange(app.client, client_id, verifier, location)
    assert tokens.status_code == 200
    assert exchange(app.client, client_id, verifier, location).json()["error"] == "invalid_grant"
    bad_state = app.client.get(
        "/auth/callback", params={"code": "fixture-code", "state": "unknown-state"}
    )
    assert bad_state.status_code == 400
    assert not app.backend.store.notes


def test_github_outage_fails_closed_without_interrupting_legacy(app):
    app.start()
    _, tokens = app.login()
    app.github.github.user_status = 503
    response = app.client.post(
        "/claude/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    assert app.rpc(app.mcp_token, "ping", path="/mcp") == {}
    assert not app.backend.store.notes
    app.github.github.user_status = 200
    assert app.rpc(tokens["access_token"], "tools/list")["tools"]


def test_changed_owner_and_explicit_local_reset_deny_old_grants(app):
    app.start()
    _, tokens = app.login()
    busy = run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json")
    assert busy.exit_code == 5
    app.stop()
    app.oauth.values["allowed_github_user_id"] = "43"
    app.start()
    denied = app.client.post(
        "/claude/mcp", json={}, headers={"Authorization": "Bearer " + tokens["access_token"]}
    )
    assert denied.status_code == 403
    assert not app.backend.store.notes
    app.stop()
    for _ in range(2):
        reset = run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json")
        assert reset.exit_code == 0
    app.oauth.values["allowed_github_user_id"] = "42"
    app.start()
    assert (
        app.client.post(
            "/claude/mcp", json={}, headers={"Authorization": "Bearer " + tokens["access_token"]}
        ).status_code
        == 401
    )
    _, fresh = app.login()
    assert app.rpc(fresh["access_token"], "tools/list")["tools"]


def test_public_oauth_rejects_host_origin_oversized_input_and_duplicate_auth(app):
    app.start()
    payload = {
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "token_endpoint_auth_method": "none",
    }
    assert app.client.post(
        "/register", json=payload, headers={"Host": "attacker.invalid"}
    ).status_code in {400, 421}
    assert (
        app.client.post(
            "/register", json=payload, headers={"Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    assert (
        app.client.post("/register", json={**payload, "client_name": "x" * 9000}).status_code == 413
    )
    assert (
        app.client.get(
            "/.well-known/oauth-authorization-server", params={"x": "a" * 8193}
        ).status_code
        == 414
    )
    _, tokens = app.login()
    headers = [("Authorization", "Bearer " + tokens["access_token"])] * 2
    denied = app.client.post("/claude/mcp", json={}, headers=headers)
    assert denied.status_code == 401
    assert not app.backend.store.notes


def test_mixed_interface_create_race_and_credential_isolation(app):
    app.start()
    _, tokens = app.login()
    oauth_token = tokens["access_token"]
    parent = app.backend.store.add_folder("Mixed interface race")
    arguments = {"parent_id": parent, "title": "One shared note", "body": "fixture"}
    baseline = app.proxy.forwarded
    first_write, overlapped = threading.Lock(), []

    def hold_first_write(method, path, query):
        # Keep the first create open until all three interface requests have
        # reached the server, so the shared create lock is actually contended.
        if method == "POST" and path == "/notes" and first_write.acquire(blocking=False):
            overlapped.append(app.proxy.wait_forwarded(baseline + 3, timeout=5))

    def create(interface):
        if interface == "actions":
            response = app.client.post(
                "/api/gpt/v1/tools/joplin_create_note",
                json=arguments,
                headers={"Authorization": "Bearer " + app.actions_token},
            )
            assert response.status_code in {200, 409}
            return response.status_code == 200
        result = app.rpc(
            oauth_token if interface == "oauth" else app.mcp_token,
            "tools/call",
            {"name": "joplin_create_note", "arguments": arguments},
            path="/claude/mcp" if interface == "oauth" else "/mcp",
        )
        if result["isError"]:
            assert result["structuredContent"]["error"]["code"] == "NOTE_ALREADY_EXISTS"
        return not result["isError"]

    app.backend.set_before_request(hold_first_write)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert sum(pool.map(create, ("oauth", "legacy", "actions"))) == 1
    finally:
        app.backend.set_before_request(None)
    assert overlapped == [True], "The three create requests did not overlap"
    assert len(app.backend.store.notes) == 1

    for path, accepted in (
        ("/mcp", app.mcp_token),
        ("/claude/mcp", oauth_token),
        ("/api/gpt/v1/tools/joplin_create_note", app.actions_token),
    ):
        for credential in (app.mcp_token, app.actions_token, oauth_token, TOKEN, "fixture-secret"):
            if credential != accepted:
                assert (
                    app.client.post(
                        path,
                        json=arguments,
                        headers={"Authorization": "Bearer " + credential},
                    ).status_code
                    == 401
                )
    assert len(app.backend.store.notes) == 1


def test_large_oauth_write_read_and_ambiguous_mutation_preserve_legacy_semantics(app):
    app.start()
    _, tokens = app.login()
    access = tokens["access_token"]
    parent = app.backend.store.add_folder("Large payload fixture")
    body = "private-large-note-canary-\u0436\U0001f4dd\n" * 32768
    app.log_canaries.add("private-large-note-canary")
    created = app.tool(
        access, "joplin_create_note", {"parent_id": parent, "title": "Large fixture", "body": body}
    )
    note_id = created["note"]["id"]
    assert app.backend.store.notes[note_id]["body"] == body
    for token, path in ((access, "/claude/mcp"), (app.mcp_token, "/mcp")):
        result = app.rpc(
            token,
            "tools/call",
            {"name": "joplin_get_note", "arguments": {"note_id": note_id}},
            path=path,
        )
        assert result["isError"] is False
        assert result["structuredContent"]["note"]["body"] == body
    attempts = []

    def fail_write(method, path, query):
        if method == "POST" and path == "/notes":
            attempts.append(path)
            return "abort"

    app.backend.set_before_request(fail_write)
    try:
        result = app.rpc(
            access,
            "tools/call",
            {
                "name": "joplin_create_note",
                "arguments": {"parent_id": parent, "title": "Ambiguous fixture"},
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "AMBIGUOUS_WRITE"
        assert len(attempts) == 1
        assert len(app.backend.store.notes) == 1
    finally:
        app.backend.set_before_request(None)
