"""MCP stdio transport for local editor and agent processes."""

from __future__ import annotations

import json
import sys
from typing import BinaryIO

from joplin_md_sync.json_safety import json_nesting_exceeds
from joplin_md_sync.mcp_server import MAX_REQUEST_BYTES, JsonObject, McpDispatcher, RpcError


def _rpc_error(request_id: object, code: int, message: str, data: object = None) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _write_message(stream: BinaryIO, message: JsonObject) -> bool:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        stream.write(encoded + b"\n")
        stream.flush()
    except (BrokenPipeError, OSError):
        return False
    return True


def _discard_line_remainder(stream: BinaryIO) -> None:
    while True:
        chunk = stream.readline(MAX_REQUEST_BYTES + 2)
        if not chunk or chunk.endswith(b"\n"):
            return


def serve_mcp_stdio(
    dispatcher: McpDispatcher,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> None:
    """Process newline-delimited MCP JSON-RPC until stdin reaches EOF."""
    source = input_stream if input_stream is not None else sys.stdin.buffer
    sink = output_stream if output_stream is not None else sys.stdout.buffer

    while True:
        raw = source.readline(MAX_REQUEST_BYTES + 2)
        if not raw:
            return
        complete_line = raw.endswith(b"\n")
        line = raw[:-1] if complete_line else raw
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > MAX_REQUEST_BYTES or (not complete_line and len(raw) > MAX_REQUEST_BYTES):
            if not complete_line:
                _discard_line_remainder(source)
            if not _write_message(sink, _rpc_error(None, -32600, "Request is too large")):
                return
            continue

        try:
            if json_nesting_exceeds(line):
                raise ValueError("JSON nesting is too deep")
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            if not _write_message(sink, _rpc_error(None, -32700, "Parse error")):
                return
            continue

        try:
            response = dispatcher.dispatch(message)
        except RpcError as exc:
            request_id = message.get("id") if isinstance(message, dict) else None
            response = _rpc_error(request_id, exc.code, str(exc), exc.data)
        if response is not None and not _write_message(sink, response):
            return
