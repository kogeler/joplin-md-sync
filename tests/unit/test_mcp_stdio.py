"""Bounded framing checks for the local MCP stdio transport."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.mcp_stdio import serve_mcp_stdio


def test_oversized_line_is_discarded_and_next_request_is_processed() -> None:
    valid = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    source = io.BytesIO(b"x" * 65 + b"\n" + valid)
    sink = io.BytesIO()
    dispatcher = mock.Mock()
    dispatcher.dispatch.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}

    with mock.patch("joplin_md_sync.mcp_stdio.MAX_REQUEST_BYTES", 64):
        serve_mcp_stdio(dispatcher, input_stream=source, output_stream=sink)

    responses = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32600
    assert responses[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    dispatcher.dispatch.assert_called_once_with(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    )
