"""ASGI I/O for the same legacy MCP and Actions HTTP processing logic."""

from __future__ import annotations

import asyncio
import io
from concurrent.futures import Executor
from dataclasses import dataclass
from email.utils import formatdate
from http import HTTPStatus
from http.client import HTTPMessage

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from joplin_md_sync.gpt_actions import GptActionsTransport
from joplin_md_sync.mcp_server import (
    HTTP_CONNECTION_TIMEOUT_SECONDS,
    MAX_REQUEST_BYTES,
    BearerTokenSource,
    LegacyHttpLogic,
    McpDispatcher,
)
from joplin_md_sync.oauth_execution import CapacityExhausted, ExecutionBudget


@dataclass
class LegacyContext:
    dispatcher: McpDispatcher
    endpoint: str
    token_source: BearerTokenSource
    allowed_origins: frozenset[str]
    actions_transport: GptActionsTransport | None
    server_address: tuple[str, int]


class IncomingBody(io.BufferedIOBase):
    """Let synchronous handlers request data after their authentication checks."""

    def __init__(self, loop: asyncio.AbstractEventLoop, receive: Receive) -> None:
        super().__init__()
        self.loop = loop
        self.receive = receive

    async def _read(self, size: int) -> bytes:
        if not 0 <= size <= MAX_REQUEST_BYTES:
            raise OSError("Invalid request body size")
        body = bytearray()
        async with asyncio.timeout(HTTP_CONNECTION_TIMEOUT_SECONDS):
            while len(body) < size:
                event = await self.receive()
                if event["type"] == "http.disconnect":
                    raise OSError("Client disconnected")
                body.extend(event.get("body", b""))
                if len(body) > size:
                    raise OSError("Request body exceeds Content-Length")
                if not event.get("more_body", False):
                    break
        return bytes(body)

    def read(self, size: int | None = -1, /) -> bytes:
        incoming = self._read(-1 if size is None else size)
        try:
            pending = asyncio.run_coroutine_threadsafe(incoming, self.loop)
        except RuntimeError:
            incoming.close()
            raise OSError("HTTP server is stopping") from None
        try:
            return pending.result(timeout=HTTP_CONNECTION_TIMEOUT_SECONDS + 1)
        except BaseException:
            pending.cancel()
            raise


class LegacyAsgiRequest(LegacyHttpLogic):
    wfile: io.BytesIO

    def __init__(self, context: LegacyContext, scope: Scope, body: IncomingBody) -> None:
        self.server = context
        self.command = scope["method"]
        self.path = scope["path"]
        if scope.get("query_string"):
            self.path += "?" + scope["query_string"].decode("ascii", "replace")
        self.client_address = scope.get("client") or ("", 0)
        self.headers = HTTPMessage()
        for name, value in scope["headers"]:
            self.headers[name.decode("latin-1")] = value.decode("latin-1")
        self.rfile = body
        self.wfile = io.BytesIO()
        self.close_connection = False
        self.status = 500
        self.response_headers: list[tuple[bytes, bytes]] = []

    def send_response(self, code: int, message: str | None = None) -> None:
        self.status = int(code)
        self.send_header("Server", self.server_version)
        self.send_header("Date", formatdate(usegmt=True))

    def send_header(self, keyword: str, value: str) -> None:
        self.response_headers.append((keyword.encode("ascii"), value.encode("latin-1")))

    def end_headers(self) -> None:
        pass

    def address_string(self) -> str:
        return self.client_address[0]

    def run(self, method: str) -> None:
        handler = getattr(self, f"do_{method}", None)
        if handler is None:
            self._empty(HTTPStatus.NOT_IMPLEMENTED)
        else:
            handler()


class LegacyAsgiApp:
    """Use a fixed worker budget, with body reads delegated to the ASGI loop."""

    def __init__(self, context: LegacyContext, workers: Executor | ExecutionBudget) -> None:
        self.context = context
        self.budget = workers if isinstance(workers, ExecutionBudget) else ExecutionBudget(workers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        loop = asyncio.get_running_loop()
        handler = LegacyAsgiRequest(self.context, scope, IncomingBody(loop, receive))
        try:
            await self.budget.run(handler.run, scope["method"])
        except CapacityExhausted:
            await Response(status_code=503, headers={"Retry-After": "1"})(scope, receive, send)
            return
        except (OSError, TimeoutError):
            await Response(status_code=400, headers={"Connection": "close"})(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": handler.status,
                "headers": handler.response_headers,
            }
        )
        await send({"type": "http.response.body", "body": handler.wfile.getvalue()})
