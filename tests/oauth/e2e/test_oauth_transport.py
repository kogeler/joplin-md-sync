"""Production socket bounds and HTTP parser rejection against the real process."""

from __future__ import annotations

import socket
import time

import pytest

from tests.oauth.e2e.test_oauth_flow import app as app

pytestmark = pytest.mark.oauth_e2e("transport")


def test_real_listener_admission_includes_sockets_without_headers(app):
    app.start()
    clients = []
    try:
        for _ in range(16):
            clients.append(socket.create_connection(("127.0.0.1", app.port), timeout=2))
        time.sleep(0.1)
        with socket.create_connection(("127.0.0.1", app.port), timeout=2) as excess:
            assert excess.recv(1) == b""
    finally:
        for client in clients:
            client.close()
    assert not app.backend.store.notes


def keep_alive_health(client: socket.socket, host: str) -> bytes:
    """Complete one keep-alive request; return its status line, or b"" if refused."""
    try:
        client.sendall(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                assert not data, "The listener closed a connection mid-response"
                return b""
            data += chunk
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        return b""
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    fields = dict(line.lower().split(b":", 1) for line in lines[1:])
    assert b"close" not in fields.get(b"connection", b"").lower()
    while len(body) < int(fields[b"content-length"]):
        chunk = client.recv(65536)
        assert chunk, "The listener truncated a keep-alive response"
        body += chunk
    return lines[0]


def admitted_connection(port: int, host: str, *, timeout: float = 2) -> socket.socket:
    """Return a connection that completed a request; a closed slot frees asynchronously."""
    deadline = time.monotonic() + timeout
    while True:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        if keep_alive_health(client, host).startswith(b"HTTP/1.1 200"):
            return client
        client.close()
        assert time.monotonic() < deadline, "The listener refused an available connection slot"
        time.sleep(0.02)


def test_real_listener_admits_exactly_sixteen_keep_alive_connections(app):
    app.start()
    host = f"127.0.0.1:{app.port}"
    clients = []
    try:
        clients.append(admitted_connection(app.port, host))
        # Uvicorn closes idle keep-alive connections after 5 s; stay well inside it.
        started = time.monotonic()
        while len(clients) < 16:
            clients.append(admitted_connection(app.port, host))
        with socket.create_connection(("127.0.0.1", app.port), timeout=2) as excess:
            assert keep_alive_health(excess, host) == b"", "A 17th connection was admitted"
        clients.pop(0).close()
        clients.append(admitted_connection(app.port, host))
        with socket.create_connection(("127.0.0.1", app.port), timeout=2) as excess:
            assert keep_alive_health(excess, host) == b"", "The replacement exceeded the bound"
        # Every admitted connection stayed open and serves another request.
        for client in clients:
            assert keep_alive_health(client, host).startswith(b"HTTP/1.1 200")
        assert time.monotonic() - started < 4
    finally:
        for client in clients:
            client.close()
    assert not app.backend.store.notes


@pytest.mark.parametrize(
    "framing",
    [
        b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
        b"Content-Length: 0\r\nContent-Length: 5\r\n",
    ],
)
def test_raw_ambiguous_framing_fails_before_side_effects(app, framing):
    app.start()
    with socket.create_connection(("127.0.0.1", app.port), timeout=2) as client:
        client.sendall(b"POST /mcp HTTP/1.1\r\nHost: fixture\r\n" + framing + b"\r\n0\r\n\r\n")
        assert client.recv(4096).startswith(b"HTTP/1.1 400")
    assert not app.backend.store.notes


def test_actual_read_deadline_covers_empty_partial_and_pipelined_requests(app):
    app.start()
    clients = []
    try:
        payloads = [
            b"",
            b"POST /claude/mcp HTTP/1.1\r\nHost: fixture",
            b"POST /mcp HTTP/1.1\r\nHost: fixture\r\nContent-Length: 20\r\n\r\n{",
            (
                "GET /.well-known/oauth-authorization-server HTTP/1.1\r\nHost: "
                + app.proxy.origin.removeprefix("https://")
                + "\r\n\r\nPOST /claude/mcp HTTP/1.1\r\nHost: fixture"
            ).encode(),
        ]
        started = time.monotonic()
        for payload in payloads:
            client = socket.create_connection(("127.0.0.1", app.port), timeout=20)
            clients.append(client)
            if payload:
                client.sendall(payload)
        for client in clients:
            while client.recv(65536):
                pass
        assert time.monotonic() - started < 19
    finally:
        for client in clients:
            client.close()
    assert not app.backend.store.notes
