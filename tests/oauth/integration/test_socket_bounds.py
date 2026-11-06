"""Real socket admission and slow-request bounds, before ASGI dispatch."""

from __future__ import annotations

import asyncio
import http.client
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from joplin_md_sync.http_app import CombinedHttpApp
from joplin_md_sync.oauth_http import BoundedH11Protocol
from tests.helpers import TOKEN
from tests.oauth.fake_github import MockGitHub
from tests.oauth.helpers import OAuthArguments, child_environment, protected_file
from tests.oauth.integration.test_provider_flow import application

REPO = Path(__file__).resolve().parents[3]


class FastTestProtocol(BoundedH11Protocol):
    max_connections = 2
    read_timeout = 0.3


@contextmanager
def running_server(app, protocol=FastTestProtocol):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            http=protocol,
            access_log=False,
            log_config=None,
            proxy_headers=False,
            ws="none",
            timeout_graceful_shutdown=1,
        )
    )

    def run():
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive(), "HTTP server exited before startup"
            assert time.monotonic() < deadline, "HTTP startup deadline exceeded"
            time.sleep(0.01)
        yield server, port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "HTTP server failed to stop"


@pytest.mark.parametrize(
    "raw_request",
    [
        b"",
        b"POST /claude/mcp HTTP/1.1\r\nHost: fixture",
        b"POST /claude/mcp HTTP/1.1\r\nHost: fixture\r\nContent-Length: 50\r\n\r\n{",
    ],
)
def test_incomplete_request_deadline(raw_request: bytes) -> None:
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        while (await receive()).get("more_body", False):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with (
        running_server(app) as (_, port),
        socket.create_connection(("127.0.0.1", port), timeout=2) as client,
    ):
        client.settimeout(2)
        if raw_request:
            client.sendall(raw_request)
        assert client.recv(1024) == b""


def test_admission_includes_connections_without_headers() -> None:
    with running_server(application(MockGitHub())) as (server, port):
        clients = []
        try:
            for _ in range(FastTestProtocol.max_connections):
                clients.append(socket.create_connection(("127.0.0.1", port), timeout=2))
            deadline = time.monotonic() + 0.2
            while len(server.server_state.connections) < FastTestProtocol.max_connections:
                assert time.monotonic() < deadline
                time.sleep(0.002)
            with socket.create_connection(("127.0.0.1", port), timeout=2) as extra:
                extra.settimeout(2)
                assert extra.recv(1024) == b""
            assert len(server.server_state.connections) <= FastTestProtocol.max_connections
        finally:
            for client in clients:
                client.close()


def test_pipelined_partial_request_has_a_read_deadline() -> None:
    with (
        running_server(application(MockGitHub())) as (_, port),
        socket.create_connection(("127.0.0.1", port), timeout=2) as client,
    ):
        client.sendall(
            b"GET /.well-known/oauth-authorization-server HTTP/1.1\r\nHost: fixture\r\n\r\n"
            b"POST /claude/mcp HTTP/1.1\r\nHost: fixture"
        )
        client.settimeout(2)
        response = bytearray()
        while data := client.recv(65536):
            response.extend(data)
        assert response.startswith(b"HTTP/1.1 200")


@pytest.mark.parametrize(
    "framing",
    [
        b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
        b"Content-Length: 0\r\nContent-Length: 5\r\n",
    ],
)
def test_ambiguous_http_framing_never_reaches_an_interface(framing) -> None:
    calls = []

    async def legacy(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"unexpected"})

    combined = CombinedHttpApp(legacy, application(MockGitHub()))
    with (
        running_server(combined) as (_, port),
        socket.create_connection(("127.0.0.1", port), timeout=2) as client,
    ):
        client.sendall(b"POST /mcp HTTP/1.1\r\nHost: fixture\r\n" + framing + b"\r\n0\r\n\r\n")
        client.settimeout(2)
        assert client.recv(65536).startswith(b"HTTP/1.1 400")
    assert not calls


def test_oauth_metadata_and_challenge_on_one_real_listener() -> None:
    async def legacy(scope, receive, send):
        assert scope["type"] == "http"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"legacy-stub"})

    combined = CombinedHttpApp(legacy, application(MockGitHub()))
    with running_server(combined) as (server, port):
        assert len(server.servers) == 1
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/mcp")
            response = connection.getresponse()
            assert response.status == 200
            assert response.read() == b"legacy-stub"
            connection.request("GET", "/.well-known/oauth-authorization-server")
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["issuer"] == "https://joplin.example.invalid/"
            connection.request("POST", "/claude/mcp", b"{}", {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 401
            assert "oauth-protected-resource" in response.getheader("WWW-Authenticate", "")
            response.read()
        finally:
            connection.close()


def test_busy_oauth_listener_port_reports_a_structured_cli_error(tmp_path) -> None:
    # Uvicorn reports its own bind failures only through the muted library
    # logger and exits; the CLI must still return its structured error.
    oauth = OAuthArguments(tmp_path)
    mcp_token = protected_file(tmp_path / "mcp-token", secrets.token_urlsafe(32))
    with socket.create_server(("127.0.0.1", 0)) as busy:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "joplin_md_sync",
                "mcp",
                "serve",
                "--json",
                "--mcp-port",
                str(busy.getsockname()[1]),
                "--auth-token-file",
                str(mcp_token),
                *oauth.argv(),
            ],
            env={**child_environment(), "JOPLIN_TOKEN": TOKEN, "PYTHONPATH": str(REPO / "src")},
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 7, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["code"] == "UNSAFE_OPERATION_BLOCKED"
    assert payload["error"]
