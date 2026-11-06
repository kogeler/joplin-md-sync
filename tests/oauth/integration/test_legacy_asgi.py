"""Execute the existing MCP/Actions assertions through the single ASGI listener."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import uvicorn

from joplin_md_sync.http_app import CombinedHttpApp
from joplin_md_sync.legacy_asgi import LegacyAsgiApp, LegacyContext
from joplin_md_sync.mcp_server import BearerTokenSource
from joplin_md_sync.oauth_http import BoundedH11Protocol
from tests.integration import test_gpt_actions, test_mcp_server
from tests.oauth.fake_github import MockGitHub
from tests.oauth.integration.test_provider_flow import application


class AsgiTestServer(LegacyContext):
    def __init__(
        self,
        address,
        dispatcher,
        *,
        endpoint="/mcp",
        token_source=None,
        allowed_origins=frozenset(),
        actions_transport=None,
        max_http_connections=16,
        connection_timeout=15.0,
    ):
        self.stopped = threading.Event()
        self.socket = socket.socket()
        self.socket.bind(address)
        self.socket.listen(max_http_connections)
        super().__init__(
            dispatcher,
            endpoint,
            token_source or BearerTokenSource(None),
            allowed_origins,
            actions_transport,
            self.socket.getsockname(),
        )
        self.workers = ThreadPoolExecutor(max_workers=max_http_connections)
        root = CombinedHttpApp(
            LegacyAsgiApp(self, self.workers), application(MockGitHub()), mcp_path=endpoint
        )

        class Protocol(BoundedH11Protocol):
            max_connections = max_http_connections
            read_timeout = connection_timeout

        self.runtime = uvicorn.Server(
            uvicorn.Config(
                root,
                http=Protocol,
                proxy_headers=False,
                access_log=False,
                log_config=None,
                ws="none",
                timeout_graceful_shutdown=2,
            )
        )

    def serve_forever(self):
        try:
            asyncio.run(self.runtime.serve(sockets=[self.socket]))
        finally:
            self.stopped.set()

    def shutdown(self):
        self.runtime.should_exit = True
        assert self.stopped.wait(5), "ASGI server failed to stop"

    def server_close(self):
        self.workers.shutdown(wait=True)
        self.socket.close()


class McpAsgiTest(test_mcp_server.McpHttpTest):
    server_factory = staticmethod(AsgiTestServer)

    def setUp(self) -> None:
        super().setUp()
        assert isinstance(self.httpd, AsgiTestServer)

    def wait_for_connection_slot(self, server) -> None:
        deadline = time.monotonic() + 2
        while not server.runtime.server_state.connections:
            assert time.monotonic() < deadline, "Connection was not admitted"
            time.sleep(0.01)


class ActionsAsgiTest(test_gpt_actions.GptActionsHttpTest):
    server_factory = staticmethod(AsgiTestServer)

    def setUp(self) -> None:
        super().setUp()
        assert isinstance(self.httpd, AsgiTestServer)
