"""Socket admission and read deadlines for the embedded HTTP runtime."""

from __future__ import annotations

import asyncio

import h11
from uvicorn.protocols.http.h11_impl import H11Protocol

from joplin_md_sync.mcp_server import (
    HTTP_CONNECTION_TIMEOUT_SECONDS,
    MAX_HTTP_CONNECTIONS,
)


class BoundedH11Protocol(H11Protocol):
    """Bound sockets before parsing; retain Uvicorn's own HTTP parser.

    Uvicorn's application concurrency limit runs after headers are complete.
    This admission check also covers connections that never send headers.
    The deadline covers each incoming request, rather than business execution.
    """

    max_connections = MAX_HTTP_CONNECTIONS
    read_timeout = HTTP_CONNECTION_TIMEOUT_SECONDS

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not isinstance(transport, asyncio.Transport):
            raise TypeError("HTTP requires a stream transport")
        self._read_deadline: asyncio.TimerHandle | None = None
        self._admitted = False
        self.transport = transport
        if len(self.connections) >= self.max_connections:
            transport.close()
            return
        self._admitted = True
        super().connection_made(transport)
        self._arm_read_deadline()

    def _arm_read_deadline(self) -> None:
        if self._read_deadline is None:
            self._read_deadline = self.loop.call_later(self.read_timeout, self.transport.close)

    def data_received(self, data: bytes) -> None:
        if not self._admitted:
            return
        self._arm_read_deadline()
        super().data_received(data)
        if self.conn.their_state is h11.DONE:
            self._cancel_read_deadline()

    def _cancel_read_deadline(self) -> None:
        if self._read_deadline is not None:
            self._read_deadline.cancel()
            self._read_deadline = None

    def on_response_complete(self) -> None:
        super().on_response_complete()
        # A pipelined request may already be buffered when Uvicorn advances
        # the parser; no subsequent data_received callback is guaranteed.
        if self.conn.their_state is not h11.DONE and not self.transport.is_closing():
            self._arm_read_deadline()

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_read_deadline()
        super().connection_lost(exc)
