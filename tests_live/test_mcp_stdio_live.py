"""Read-only MCP stdio acceptance against the ephemeral Joplin profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import urllib.parse
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from joplin_md_sync.config import build_client  # noqa: E402
from joplin_md_sync.mcp_server import MCP_PROTOCOL_VERSION  # noqa: E402
from tests_live.ephemeral_joplin import running_joplin  # noqa: E402


def _cli_command() -> list[str]:
    executable = os.environ.get("JOPLIN_MD_SYNC_LIVE_EXECUTABLE")
    if executable:
        path = Path(executable).resolve()
        if not path.is_file():
            raise RuntimeError(f"live executable not found: {path}")
        return [str(path)]
    return [sys.executable, "-m", "joplin_md_sync"]


class LiveMcpStdioTest(unittest.TestCase):
    def test_stdio_process_reads_running_joplin(self) -> None:
        runtime = running_joplin()
        token = runtime.token_file.read_text(encoding="utf-8").strip()
        api = build_client(
            token_file=str(runtime.token_file),
            cli_base_url=runtime.base_url,
            timeout=5.0,
            discovery_timeout=0.25,
        )
        if not api.ping():
            raise RuntimeError(f"unexpected Joplin ping response from {api.base_url}")
        split = urllib.parse.urlsplit(api.base_url)
        if split.hostname not in {"127.0.0.1", "::1", "localhost"} or split.port is None:
            raise RuntimeError(f"live Joplin must use an explicit loopback port: {api.base_url}")

        messages: list[dict[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "joplin-md-sync-live-stdio", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "joplin_list_notebooks",
                    "arguments": {"limit": 1},
                },
            },
        ]
        input_text = "".join(json.dumps(message) + "\n" for message in messages)
        env = dict(os.environ)
        env.pop("JOPLIN_TOKEN", None)
        env.pop("JOPLIN_BASE_URL", None)
        env.pop("JOPLIN_PORT", None)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(SRC) if not existing_pythonpath else str(SRC) + os.pathsep + existing_pythonpath
        )
        process = subprocess.Popen(
            [
                *_cli_command(),
                "mcp",
                "stdio",
                "--token",
                token,
                "--port",
                str(split.port),
                "--retry-timeout",
                "2",
                "--quiet",
            ],
            cwd=REPO,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("live MCP stdio process did not stop after stdin EOF") from None

        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn(token, stdout)
        self.assertNotIn(token, stderr)
        responses = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual([response.get("id") for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        tools = responses[1]["result"]["tools"]
        self.assertIn("joplin_list_notebooks", {tool["name"] for tool in tools})
        result = responses[2]["result"]
        self.assertFalse(result["isError"], result["structuredContent"])
        self.assertIn("notebooks", result["structuredContent"])


if __name__ == "__main__":
    unittest.main()
