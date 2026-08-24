"""Local MCP stdio transport and CLI contract."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from joplin_md_sync.cli import build_parser  # noqa: E402
from joplin_md_sync.mcp_server import MCP_PROTOCOL_VERSION  # noqa: E402
from tests.helpers import TOKEN, WorkspaceTestCase, run_cli  # noqa: E402


class McpStdioCliTest(WorkspaceTestCase):
    def test_serve_remains_the_network_listener_mode(self) -> None:
        args = build_parser().parse_args(["mcp", "serve"])
        self.assertEqual(args.mcp_command, "serve")
        self.assertEqual((args.host, args.mcp_port, args.mcp_path), ("127.0.0.1", 8765, "/mcp"))

        with mock.patch("joplin_md_sync.mcp_server.serve_mcp_http") as serve_http:
            result = run_cli("mcp", "serve", "--quiet")
        self.assertEqual(result.exit_code, 0)
        serve_http.assert_called_once()
        self.assertEqual(serve_http.call_args.kwargs["port"], 8765)

    def test_token_is_required_and_joplin_port_defaults_to_41184(self) -> None:
        args = build_parser().parse_args(["mcp", "stdio", "--token", TOKEN])
        self.assertEqual(args.port, 41184)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["mcp", "stdio"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--token", stderr.getvalue())

    def test_stdio_has_no_http_listener_or_interface_bearer_options(self) -> None:
        args = build_parser().parse_args(["mcp", "stdio", "--token", TOKEN])
        for attribute in ("mcp_port", "auth_token_file", "gpt_actions_token_file"):
            self.assertFalse(hasattr(args, attribute), attribute)

        for option in ("--auth-token-file", "--gpt-actions-token-file"):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["mcp", "stdio", "--token", TOKEN, option, "unused"])
            self.assertEqual(raised.exception.code, 2)

        with (
            mock.patch("joplin_md_sync.mcp_server.serve_mcp_http") as serve_http,
            mock.patch("joplin_md_sync.mcp_stdio.serve_mcp_stdio") as serve_stdio,
        ):
            result = run_cli("mcp", "stdio", "--token", TOKEN, "--quiet")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "")
        serve_stdio.assert_called_once()
        serve_http.assert_not_called()

    def test_configuration_errors_never_write_to_protocol_stdout(self) -> None:
        result = run_cli("mcp", "stdio", "--token", TOKEN, "--port", "0")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stdout, "")
        self.assertIn("--port", result.stderr)
        self.assertNotIn(TOKEN, result.stderr)

    def test_lifecycle_tools_live_call_and_protocol_errors(self) -> None:
        port = urllib.parse.urlsplit(self.server.base_url).port
        self.assertIsNotNone(port)
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "joplin_list_notebooks", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 5, "method": "unknown/method"},
        ]
        input_text = "".join(json.dumps(message) + "\n" for message in requests) + "{broken\n"
        env = dict(os.environ)
        env.pop("JOPLIN_TOKEN", None)
        env.pop("JOPLIN_BASE_URL", None)
        env.pop("JOPLIN_PORT", None)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(SRC) if not existing_pythonpath else str(SRC) + os.pathsep + existing_pythonpath
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "joplin_md_sync",
                "mcp",
                "stdio",
                "--token",
                TOKEN,
                "--port",
                str(port),
                "--retry-timeout",
                "0",
                "--quiet",
            ],
            cwd=ROOT,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(TOKEN, completed.stdout)
        self.assertNotIn(TOKEN, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response.get("id") for response in responses], [1, 2, 3, 4, 5, None])
        self.assertEqual(responses[0]["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(responses[1]["result"], {})
        tools = responses[2]["result"]["tools"]
        self.assertIn("joplin_list_notebooks", {tool["name"] for tool in tools})
        notebooks = responses[3]["result"]["structuredContent"]["notebooks"]
        self.assertTrue(any(notebook["title"] == "Work" for notebook in notebooks))
        self.assertEqual(responses[4]["error"]["code"], -32601)
        self.assertEqual(responses[5]["error"]["code"], -32700)


if __name__ == "__main__":
    import unittest

    unittest.main()
