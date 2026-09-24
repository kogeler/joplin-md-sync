"""Local MCP stdio transport and CLI contract."""

from __future__ import annotations

import contextlib
import io
import json
import logging
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
from joplin_md_sync.errors import ApiError  # noqa: E402
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

    def _token_file(self, content: str = TOKEN) -> Path:
        path = Path(self._tmp.name) / "joplin-token"
        path.write_bytes(f"{content}\n".encode("ascii"))
        if os.name == "posix":
            path.chmod(0o600)
        return path

    def test_token_options_are_optional_exclusive_and_port_defaults_to_41184(self) -> None:
        args = build_parser().parse_args(["mcp", "stdio"])
        self.assertEqual((args.token, args.token_file, args.port), (None, None, 41184))
        args = build_parser().parse_args(["mcp", "stdio", "--token-file", "token"])
        self.assertEqual((args.token, args.token_file), (None, "token"))

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["mcp", "stdio", "--token", TOKEN, "--token-file", "x"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())

    def test_command_line_and_environment_sources_conflict_before_serving(self) -> None:
        token_file = self._token_file("file-secret-0123456789")
        for arguments in (("--token", TOKEN), ("--token-file", str(token_file))):
            with (
                self.subTest(option=arguments[0]),
                mock.patch("joplin_md_sync.config.read_protected_token_line") as read,
                mock.patch("joplin_md_sync.mcp_stdio.serve_mcp_stdio") as serve_stdio,
            ):
                result = run_cli("mcp", "stdio", *arguments, env={"JOPLIN_TOKEN": TOKEN})
                self.assertEqual(result.exit_code, 4)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"({arguments[0]})", result.stderr)
                self.assertIn("JOPLIN_TOKEN", result.stderr)
                self.assertNotIn(TOKEN, result.stderr)
                self.assertNotIn("file-secret", result.stderr)
                read.assert_not_called()
                serve_stdio.assert_not_called()

    def test_missing_token_names_both_sources_and_blank_environment_is_unset(self) -> None:
        for blank in (None, "", "   "):
            env = None if blank is None else {"JOPLIN_TOKEN": blank}
            with (
                self.subTest(blank=blank),
                mock.patch("joplin_md_sync.mcp_stdio.serve_mcp_stdio") as serve_stdio,
            ):
                result = run_cli("mcp", "stdio", env=env)
                self.assertEqual(result.exit_code, 4)
                self.assertEqual(result.stdout, "")
                self.assertIn("no Joplin token configured", result.stderr)
                self.assertIn("JOPLIN_TOKEN", result.stderr)
                self.assertIn("--token-file", result.stderr)
                serve_stdio.assert_not_called()

                result = run_cli("mcp", "stdio", "--token", TOKEN, "--quiet", env=env)
                self.assertEqual(result.exit_code, 0, result.stderr)
                serve_stdio.assert_called_once()

    def test_file_and_environment_tokens_are_redacted(self) -> None:
        def leak_token(_dispatcher: object) -> None:
            logging.getLogger("joplin_md_sync").error("stdio log mentions %s", TOKEN)
            raise ApiError(f"transport failed for {TOKEN}")

        token_file = self._token_file()
        for arguments, env in (
            (("--token-file", str(token_file)), None),
            ((), {"JOPLIN_TOKEN": TOKEN}),
        ):
            with (
                self.subTest(arguments=arguments),
                mock.patch("joplin_md_sync.mcp_stdio.serve_mcp_stdio", side_effect=leak_token),
            ):
                result = run_cli("mcp", "stdio", *arguments, env=env)
                self.assertEqual(result.exit_code, 4)
                self.assertEqual(result.stdout, "")
                self.assertIn("stdio log mentions ***", result.stderr)
                self.assertIn("transport failed for ***", result.stderr)
                self.assertNotIn(TOKEN, result.stderr)

    def test_internal_error_traceback_is_redacted_on_stderr_and_log_file(self) -> None:
        log_file = Path(self._tmp.name) / "stdio.log"
        with mock.patch(
            "joplin_md_sync.mcp_stdio.serve_mcp_stdio",
            side_effect=RuntimeError(f"transport failed for {TOKEN}"),
        ):
            result = run_cli(
                "mcp",
                "stdio",
                "--token-file",
                str(self._token_file()),
                "--log-file",
                str(log_file),
            )
        self.assertEqual(result.exit_code, 9)
        self.assertEqual(result.stdout, "")
        log_text = log_file.read_text(encoding="utf-8")
        for output in (result.stderr, log_text):
            self.assertIn("Traceback", output)
            self.assertIn("RuntimeError: transport failed for ***", output)
            self.assertNotIn(TOKEN, output)

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

    def _run_stdio_process(
        self,
        token_arguments: tuple[str, ...],
        token_env: dict[str, str],
        input_text: str,
    ) -> subprocess.CompletedProcess[str]:
        port = urllib.parse.urlsplit(self.server.base_url).port
        self.assertIsNotNone(port)
        env = dict(os.environ)
        env.pop("JOPLIN_TOKEN", None)
        env.pop("JOPLIN_BASE_URL", None)
        env.pop("JOPLIN_PORT", None)
        env.update(token_env)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(SRC) if not existing_pythonpath else str(SRC) + os.pathsep + existing_pythonpath
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "joplin_md_sync",
                "mcp",
                "stdio",
                *token_arguments,
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

    def test_token_file_and_environment_each_serve_joplin_tool_calls(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "joplin_list_notebooks", "arguments": {}},
        }
        for token_arguments, token_env in (
            (("--token-file", str(self._token_file())), {}),
            ((), {"JOPLIN_TOKEN": f" {TOKEN} "}),
        ):
            with self.subTest(arguments=token_arguments, env=sorted(token_env)):
                completed = self._run_stdio_process(
                    token_arguments, token_env, json.dumps(request) + "\n"
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn(TOKEN, completed.stdout)
                self.assertNotIn(TOKEN, completed.stderr)
                (response,) = [json.loads(line) for line in completed.stdout.splitlines()]
                result = response["result"]
                self.assertFalse(result["isError"], result["structuredContent"])
                notebooks = result["structuredContent"]["notebooks"]
                self.assertTrue(any(notebook["title"] == "Work" for notebook in notebooks))

    def test_lifecycle_tools_live_call_and_protocol_errors(self) -> None:
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
        completed = self._run_stdio_process(("--token", TOKEN), {}, input_text)

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
