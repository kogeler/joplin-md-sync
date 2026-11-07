from __future__ import annotations

import hashlib
import http.server
import io
import json
import logging
import os
import pty
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import warnings
from pathlib import Path
from unittest import mock

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from joplin_terminal_common import (  # noqa: E402
    ProfileLock,
    SecretRedactor,
    ToolError,
    child_environment,
    ping_mcp,
    safe_command,
)
from joplin_terminal_installer import (  # noqa: E402
    cli,
    commands,
    common,
    credentials,
    encryption,
    managed,
    models,
    operations,
    releases,
    service,
    sync,
)
from joplin_terminal_installer import lifecycle as application  # noqa: E402

GPT_ACTIONS_TOKEN = "g" * 43
MCP_TOKEN = "m" * 43


def write_installed_units(tool: application.Installer) -> None:
    service.install_unit(
        tool.paths, tool.args, tool.paths.deploy_dir / "run_joplin_terminal.py", Path("/node")
    )
    service.install_adapter_unit(tool.paths, tool.args)


class ParserTests(unittest.TestCase):
    def test_cli_overrides_environment(self) -> None:
        env = {
            "HOME": "/tmp/home",
            "JOPLIN_SYNC_TARGET": "webdav",
            "JOPLIN_SYNC_LOCATION": "https://env.invalid/Joplin",
            "JOPLIN_SYNC_USERNAME": "env-user",
            "JOPLIN_API_PORT": "41234",
        }
        args = cli.build_parser(env).parse_args(
            [
                "--sync-target",
                "nextcloud",
                "--sync-location",
                "https://cli.invalid/Joplin",
                "--sync-username",
                "cli-user",
                "--api-port",
                "42345",
            ]
        )
        self.assertEqual(args.sync_target, "nextcloud")
        self.assertEqual(args.sync_location, "https://cli.invalid/Joplin")
        self.assertEqual(args.sync_username, "cli-user")
        self.assertEqual(args.api_port, 42345)

    def test_github_oauth_environment_is_a_strict_boolean_and_cli_overrides_it(self) -> None:
        for value, choice, explicit in (
            ("1", (True, False), "github_oauth"),
            ("TRUE", (True, False), "github_oauth"),
            ("yes", (True, False), "github_oauth"),
            ("0", (False, True), "no_github_oauth"),
            ("false", (False, True), "no_github_oauth"),
            (" No ", (False, True), "no_github_oauth"),
            ("", (False, False), None),
        ):
            with self.subTest(value=value):
                args = cli.build_parser({"HOME": "/tmp/home", "JOPLIN_GITHUB_OAUTH": value})
                args = args.parse_args([])
                self.assertEqual((args.github_oauth, args.no_github_oauth), choice)
                self.assertEqual(
                    args.explicit_options & {"github_oauth", "no_github_oauth"},
                    {explicit} if explicit else set(),
                )
                self.assertEqual(args.command_line_options, set())
        for value in ("maybe", "on", "2"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ToolError, "JOPLIN_GITHUB_OAUTH must be one of"),
            ):
                cli.build_parser({"HOME": "/tmp/home", "JOPLIN_GITHUB_OAUTH": value})
        for value, flag, choice, explicit in (
            ("false", "--github-oauth", (True, False), "github_oauth"),
            ("true", "--no-github-oauth", (False, True), "no_github_oauth"),
        ):
            with self.subTest(value=value, flag=flag):
                parser = cli.build_parser({"HOME": "/tmp/home", "JOPLIN_GITHUB_OAUTH": value})
                args = parser.parse_args([flag])
                self.assertEqual((args.github_oauth, args.no_github_oauth), choice)
                self.assertEqual(
                    args.explicit_options & {"github_oauth", "no_github_oauth"}, {explicit}
                )
        for value in ("", "true", "false"):
            with self.subTest(conflict=value):
                parser = cli.build_parser({"HOME": "/tmp/home", "JOPLIN_GITHUB_OAUTH": value})
                args = parser.parse_args(["--github-oauth", "--no-github-oauth"])
                with self.assertRaisesRegex(ToolError, "cannot be combined"):
                    cli.validate_args(args)

    def test_defaults_resolve_latest_releases(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args([])
        self.assertEqual(args.api_port, 41185)
        self.assertEqual(args.mcp_port, 8765)
        self.assertEqual(args.joplin_version, "latest")
        self.assertEqual(args.joplin_md_sync_version, "latest")
        self.assertEqual(args.sync_interval, 300)
        self.assertFalse(args.upgrade)
        self.assertFalse(args.allow_remote_mcp)
        self.assertNotIn("mcp_auth_token", vars(args))
        self.assertFalse(args.purge)
        self.assertFalse(args.yes)

    def test_help_describes_operational_defaults(self) -> None:
        help_text = " ".join(cli.build_parser({"HOME": "/tmp/home"}).format_help().split())
        for expected in (
            "prompted when not configured or supplied",
            "default: https://s3.amazonaws.com/",
            "default: disabled",
            "default: 41185",
            "default: 300",
            "default: latest",
            "default: ~/.local",
            "default: 8765",
            "leave both services stopped",
            "without reading secrets or changing state",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, help_text)

    def test_upgrade_accepts_independent_version_overrides(self) -> None:
        requested_version = "3.7.1"
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
            ["--upgrade", "--joplin-version", requested_version]
        )
        cli.validate_args(args)
        self.assertTrue(args.upgrade)
        self.assertEqual(args.joplin_version, requested_version)
        self.assertEqual(args.joplin_md_sync_version, "latest")

    def test_unreleased_mcp_version_name_is_not_supported(self) -> None:
        with (
            mock.patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            cli.build_parser({"HOME": "/tmp/home"}).parse_args(["--mcp-version", "1.2.0"])

    def test_rejects_unsupported_sync_interval(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
            [
                "--sync-target",
                "nextcloud",
                "--sync-location",
                "https://cloud.invalid/Joplin",
                "--sync-username",
                "u",
                "--sync-interval",
                "123",
            ]
        )
        with self.assertRaisesRegex(ToolError, "must be one of"):
            cli.validate_args(args)

    def test_rejects_port_collision(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
            ["--upgrade", "--api-port", "41185", "--mcp-port", "41185"]
        )
        with self.assertRaisesRegex(ToolError, "must be different"):
            cli.validate_args(args)

    def test_generated_mcp_token_requirement(self) -> None:
        with self.assertRaisesRegex(ToolError, "at least 32"):
            credentials.validate_mcp_auth_token("c2hvcnQ")
        credentials.validate_mcp_auth_token(MCP_TOKEN)

    def test_purge_does_not_require_sync_target_and_rejects_upgrade(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(["--purge"])
        cli.validate_args(args)
        combined = cli.build_parser({"HOME": "/tmp/home"}).parse_args(["--purge", "--upgrade"])
        with self.assertRaisesRegex(ToolError, "cannot be combined"):
            cli.validate_args(combined)

    def test_all_current_joplin_cli_targets_are_declared(self) -> None:
        self.assertEqual(
            {name: target.target_id for name, target in models.SYNC_TARGETS.items()},
            {
                "none": 0,
                "filesystem": 2,
                "onedrive": 3,
                "nextcloud": 5,
                "webdav": 6,
                "dropbox": 7,
                "s3": 8,
                "joplin-server": 9,
                "joplin-cloud": 10,
                "joplin-server-saml": 11,
            },
        )

    def test_browser_targets_require_interactive_installation(self) -> None:
        for target in ("onedrive", "dropbox", "joplin-cloud", "joplin-server-saml"):
            extra = ["--sync-location", "https://server.invalid"] if target.endswith("saml") else []
            args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
                ["--sync-target", target, "--non-interactive", *extra]
            )
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(ToolError, "interactive terminal"),
            ):
                cli.validate_args(args)

    def test_interactive_webdav_can_prompt_for_password(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
            [
                "--sync-target",
                "webdav",
                "--sync-location",
                "https://dav.invalid/Joplin",
                "--sync-username",
                "user",
            ]
        )
        cli.validate_args(args)
        configuration = sync.resolve_sync_configuration(
            args,
            SecretRedactor(),
            getpass_fn=lambda _prompt: "sync-secret",
        )
        self.assertEqual(
            {setting.name: setting.value for setting in configuration.settings},
            {
                "sync.6.path": "https://dav.invalid/Joplin",
                "sync.6.username": "user",
                "sync.6.password": "sync-secret",
            },
        )

    def test_noninteractive_webdav_requires_complete_credentials(self) -> None:
        parser = cli.build_parser({"HOME": "/tmp/home"})
        username_only = parser.parse_args(
            [
                "--sync-target",
                "webdav",
                "--sync-location",
                "https://dav.invalid/Joplin",
                "--sync-username",
                "user",
                "--non-interactive",
            ]
        )
        with self.assertRaisesRegex(ToolError, "require --sync-secret-file"):
            cli.validate_args(username_only)

        secret_only = parser.parse_args(
            [
                "--sync-target",
                "webdav",
                "--sync-location",
                "https://dav.invalid/Joplin",
                "--sync-secret-file",
                "/tmp/secret",
            ]
        )
        with self.assertRaisesRegex(ToolError, "requires --sync-username"):
            cli.validate_args(secret_only)

    def test_yes_is_only_valid_for_purge(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(["--yes"])
        with self.assertRaisesRegex(ToolError, "only valid with --purge"):
            cli.validate_args(args)


class SecretTests(unittest.TestCase):
    def test_hidden_input_refuses_getpass_echo_fallback(self) -> None:
        def fallback(_prompt: str) -> str:
            warnings.warn(
                "fixture TTY cannot disable echo", credentials.getpass.GetPassWarning, stacklevel=2
            )
            self.fail("fallback must stop before reading a secret")

        with (
            mock.patch.object(credentials.getpass, "getpass", side_effect=fallback),
            self.assertRaisesRegex(ToolError, "hidden terminal input is unavailable"),
        ):
            credentials.hidden_input("Fixture credential: ")

    def test_secret_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fifo = Path(temporary) / "credential"
            os.mkfifo(fifo, 0o600)
            code = (
                "from pathlib import Path; import sys\n"
                "from joplin_terminal_installer.credentials import load_secret_file\n"
                "from joplin_terminal_installer.common import ToolError\n"
                "try: load_secret_file(Path(sys.argv[1]), 'fixture')\n"
                "except ToolError: raise SystemExit(0)\n"
                "raise SystemExit(1)\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, str(fifo)],
                env={"PATH": os.environ["PATH"], "PYTHONPATH": str(TOOLS_DIR)},
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_s3_defaults_prompt_for_both_keys_without_echo(self) -> None:
        args = cli.build_parser({}).parse_args(
            [
                "--sync-target",
                "s3",
                "--sync-location",
                "fixture-bucket",
                "--s3-region",
                "custom-region-1",
            ]
        )
        cli.validate_args(args)
        access = "fixture-hidden-access"
        secret = "fixture-hidden-secret"
        pid, fd = pty.fork()
        if pid == 0:
            try:
                config = sync.resolve_sync_configuration(args, SecretRedactor())
                settings = {setting.name: setting.value for setting in config.settings}
                success = (
                    settings["sync.8.username"] == access and settings["sync.8.password"] == secret
                )
            except BaseException:
                os._exit(1)
            os._exit(0 if success else 2)
        output = bytearray()
        pending = [(b"S3 access key: ", access), (b"S3 secret key: ", secret)]
        reaped = False
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if pending and pending[0][0] in output:
                    _prompt, value = pending.pop(0)
                    os.write(fd, (value + "\n").encode())
            else:
                self.fail("hidden S3 prompts did not finish")
            _pid, status = os.waitpid(pid, 0)
            reaped = True
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertFalse(pending)
            self.assertNotIn(access.encode(), output)
            self.assertNotIn(secret.encode(), output)
        finally:
            if not reaped:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(fd)

    def test_s3_optional_files_replace_only_their_corresponding_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = {"S3 access key: ": "fixture-access", "S3 secret key: ": "fixture-secret"}
            for name, value in (("access", "fixture-access"), ("secret", "fixture-secret")):
                common.atomic_write(root / name, (value + "\n").encode(), 0o600)
            for access_file, secret_file in ((True, False), (False, True), (True, True)):
                with self.subTest(access_file=access_file, secret_file=secret_file):
                    options = [
                        "--sync-target",
                        "s3",
                        "--sync-location",
                        "fixture-bucket",
                        "--s3-region",
                        "custom-region-1",
                    ]
                    if access_file:
                        options += ["--sync-username-file", str(root / "access")]
                    if secret_file:
                        options += ["--sync-secret-file", str(root / "secret")]
                    if access_file and secret_file:
                        options += ["--non-interactive"]
                    args = cli.build_parser({}).parse_args(options)
                    cli.validate_args(args)
                    prompt = mock.Mock(side_effect=lambda label: keys[label])
                    config = sync.resolve_sync_configuration(
                        args, SecretRedactor(), getpass_fn=prompt
                    )
                    values = {setting.name: setting.value for setting in config.settings}
                    self.assertEqual(values["sync.8.username"], "fixture-access")
                    self.assertEqual(values["sync.8.password"], "fixture-secret")
                    expected = []
                    if not access_file:
                        expected.append(mock.call("S3 access key: "))
                    if not secret_file:
                        expected.append(mock.call("S3 secret key: "))
                    self.assertEqual(prompt.call_args_list, expected)

    def test_s3_noninteractive_missing_keys_fails_before_reading_or_prompting(self) -> None:
        for options in (
            [],
            ["--sync-username-file", "/not-read/access"],
            ["--sync-secret-file", "/not-read/secret"],
        ):
            args = cli.build_parser({}).parse_args(
                [
                    "--sync-target",
                    "s3",
                    "--sync-location",
                    "fixture-bucket",
                    "--s3-region",
                    "custom-region-1",
                    "--non-interactive",
                    *options,
                ]
            )
            with mock.patch.object(credentials, "load_secret_file") as read_file:
                with self.assertRaises(ToolError):
                    cli.validate_args(args)
            read_file.assert_not_called()

    def test_settings_use_stdin_not_process_arguments_and_preserve_json_types(self) -> None:
        runner = commands.CommandRunner(SecretRedactor(), os.environ)
        paths = mock.Mock()
        # Real subprocess: JSON is delivered on a pipe, not a file or argv.
        command = [
            sys.executable,
            "-c",
            "import sys,json; print(json.dumps({'argv':sys.argv[1:], 'settings':json.load(sys.stdin)}))",
        ]
        original_run = runner.run
        observed: list[dict[str, object]] = []

        def capture(*args, **kwargs):
            result = original_run(*args, **kwargs)
            observed.append(json.loads(result.stdout))
            return result

        secret = 'synthetic-password-"-λ-\\'
        with (
            mock.patch.object(sync, "joplin_command", return_value=command),
            mock.patch.object(runner, "run", side_effect=capture),
        ):
            for name, value in (
                ("sync.8.password", secret),
                ("sync.8.username", "access-key"),
                ("sync.8.forcePathStyle", "false"),
                ("api.port", "49151"),
            ):
                sync.write_setting(runner, paths, name, value, secret=True)
        self.assertTrue(all(result["argv"] == [] for result in observed))
        self.assertEqual(observed[0]["settings"], {"sync.8.password": secret})
        self.assertEqual(observed[2]["settings"], {"sync.8.forcePathStyle": False})
        self.assertEqual(observed[3]["settings"], {"api.port": 49151})

    def test_s3_access_key_file_is_protected_and_never_a_child_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key_file = root / "key"
            common.atomic_write(key_file, b"fixture-access-key\n", 0o600)
            args = cli.build_parser({}).parse_args(
                [
                    "--sync-target",
                    "s3",
                    "--sync-location",
                    "notes-bucket",
                    "--s3-region",
                    "custom-region-1",
                    "--sync-username-file",
                    str(key_file),
                ]
            )
            cli.validate_args(args)
            config = sync.resolve_sync_configuration(
                args, SecretRedactor(), getpass_fn=lambda _: "fixture-secret"
            )
            self.assertIn(
                models.SyncSetting("sync.8.username", "fixture-access-key"), config.settings
            )
            key_file.chmod(0o644)
            with self.assertRaisesRegex(ToolError, "group or others"):
                sync.resolve_sync_configuration(args, SecretRedactor())
            args.sync_username = "duplicate"
            with self.assertRaisesRegex(ToolError, "exactly one"):
                cli.validate_args(args)

    def test_child_environment_removes_every_service_secret(self) -> None:
        env = child_environment(
            {
                "PATH": "/usr/bin",
                "JOPLIN_TOKEN": "joplin-secret",
                "JOPLIN_GPT_ACTIONS_TOKEN": "actions-secret",
                "JOPLIN_MCP_AUTH_TOKEN": "mcp-secret",
                "JOPLIN_SYNC_USERNAME": "fixture-access-key",
            }
        )

        self.assertEqual(env["PATH"], "/usr/bin")
        for name in (
            "JOPLIN_TOKEN",
            "JOPLIN_GPT_ACTIONS_TOKEN",
            "JOPLIN_MCP_AUTH_TOKEN",
            "JOPLIN_SYNC_USERNAME",
        ):
            self.assertNotIn(name, env)

    def test_terminal_prompt_eof_is_a_tool_error(self) -> None:
        with (
            mock.patch("builtins.open", side_effect=OSError),
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaisesRegex(ToolError, "interactive input is unavailable"),
        ):
            cli.terminal_input("Continue? ")

    def test_secret_file_precedes_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "secret"
            secret_file.write_text("file-secret\n")
            secret_file.chmod(0o600)
            prompt = mock.Mock(return_value="prompt-secret")
            self.assertEqual(
                credentials.resolve_secret(
                    str(secret_file),
                    "--secret-file",
                    "Password: ",
                    non_interactive=False,
                    getpass_fn=prompt,
                ),
                "file-secret",
            )
            prompt.assert_not_called()

    def test_secret_prompt_is_used_without_file(self) -> None:
        prompt = mock.Mock(return_value="prompt-secret")
        self.assertEqual(
            credentials.resolve_secret(
                None,
                "--secret-file",
                "Password: ",
                non_interactive=False,
                getpass_fn=prompt,
            ),
            "prompt-secret",
        )

    def test_non_interactive_never_prompts(self) -> None:
        prompt = mock.Mock()
        with self.assertRaisesRegex(ToolError, "required secret is missing"):
            credentials.resolve_secret(
                None,
                "--secret-file",
                "Password: ",
                non_interactive=True,
                getpass_fn=prompt,
            )
        prompt.assert_not_called()

    def test_secret_prompt_eof_is_a_tool_error(self) -> None:
        with self.assertRaisesRegex(ToolError, "interactive input.*unavailable"):
            credentials.resolve_secret(
                None,
                "--secret-file",
                "Password: ",
                non_interactive=False,
                getpass_fn=mock.Mock(side_effect=EOFError),
            )

    def test_secret_file_rejects_unsafe_mode_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_file = root / "secret"
            secret_file.write_text("secret\n")
            secret_file.chmod(0o644)
            with self.assertRaisesRegex(ToolError, "group or others"):
                credentials.load_secret_file(secret_file, "sync secret")
            secret_file.chmod(0o600)
            link = root / "link"
            link.symlink_to(secret_file)
            with self.assertRaisesRegex(ToolError, "could not read"):
                credentials.load_secret_file(link, "sync secret")

    def test_safe_command_and_log_filter(self) -> None:
        secret = "highly-secret-value"
        self.assertNotIn(secret, safe_command(["tool", "--password", secret], [secret]))
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(SecretRedactor([secret]))
        logger = logging.getLogger("secret-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info("command failed with %s", secret)
        self.assertNotIn(secret, stream.getvalue())
        self.assertIn("[REDACTED]", stream.getvalue())

    def test_command_failure_does_not_leak_secret(self) -> None:
        secret = "command-secret"
        redactor = SecretRedactor([secret])
        runner = commands.CommandRunner(redactor, {"PATH": os.environ["PATH"]})
        with (
            self.assertLogs("joplin-terminal-installer", level="DEBUG") as captured,
            self.assertRaises(ToolError) as raised,
        ):
            runner.run(
                [
                    sys.executable,
                    "-c",
                    f"import sys; print('{secret}', file=sys.stderr); sys.exit(2)",
                    secret,
                ],
                secrets=(secret,),
            )
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, "\n".join(captured.output))

    def test_long_sensitive_command_has_bounded_output_and_safe_heartbeat(self) -> None:
        private_text = "private notebook title"
        runner = commands.CommandRunner(SecretRedactor(), {"PATH": os.environ["PATH"]})
        code = (
            "import sys,time;"
            f"data={private_text!r}*50000;"
            "sys.stdout.write(data);sys.stdout.flush();"
            "sys.stderr.write(data);sys.stderr.flush();"
            "time.sleep(0.08)"
        )
        with self.assertLogs("joplin-terminal-installer", level="INFO") as captured:
            result = runner.run_long(
                [sys.executable, "-c", code],
                heartbeat_label="initial sync",
                timeout=3,
                heartbeat_interval=0.01,
                max_output_bytes=1024,
                sensitive_output=True,
            )
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout.encode()), 1024)
        self.assertLessEqual(len(result.stderr.encode()), 1024)
        logs = "\n".join(captured.output)
        self.assertIn("initial sync still running", logs)
        self.assertNotIn(private_text, logs)

    def test_service_tokens_are_generated_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = cli.build_parser(env).parse_args([])
            paths = cli.build_paths(args, env)
            with mock.patch.object(
                credentials.secrets,
                "token_urlsafe",
                side_effect=[MCP_TOKEN, GPT_ACTIONS_TOKEN],
            ) as generate:
                mcp_token, actions_token = credentials.resolve_service_tokens(
                    paths,
                    SecretRedactor(),
                )
            self.assertEqual((mcp_token, actions_token), (MCP_TOKEN, GPT_ACTIONS_TOKEN))
            self.assertEqual(generate.call_count, 2)

            credentials.configure_mcp_auth_token(paths, mcp_token)
            credentials.configure_gpt_actions_token(paths, actions_token)
            self.assertEqual(stat.S_IMODE(paths.mcp_auth_token_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(paths.gpt_actions_token_file.stat().st_mode), 0o600)

            with mock.patch.object(
                credentials.secrets,
                "token_urlsafe",
                side_effect=AssertionError("valid tokens must be preserved"),
            ):
                preserved = credentials.resolve_service_tokens(paths, SecretRedactor())
            self.assertEqual(preserved, (MCP_TOKEN, GPT_ACTIONS_TOKEN))

    def test_invalid_existing_tokens_are_rejected_without_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = cli.build_parser(env).parse_args([])
            paths = cli.build_paths(args, env)
            paths.mcp_config_dir.mkdir(parents=True)
            paths.mcp_auth_token_file.write_text("c2hvcnQ\n", encoding="utf-8")
            with (
                mock.patch.object(
                    credentials.secrets,
                    "token_urlsafe",
                    side_effect=AssertionError("invalid token must not be rotated"),
                ),
                self.assertRaisesRegex(ToolError, "at least 32"),
            ):
                credentials.resolve_mcp_auth_token(paths, SecretRedactor())

            paths.mcp_auth_token_file.write_text(MCP_TOKEN + "\n", encoding="utf-8")
            paths.gpt_actions_token_file.write_text("g" * 36 + "\n", encoding="utf-8")
            with (
                mock.patch.object(
                    credentials.secrets,
                    "token_urlsafe",
                    side_effect=AssertionError("invalid token must not be rotated"),
                ),
                self.assertRaisesRegex(ToolError, "32 bytes"),
            ):
                credentials.resolve_gpt_actions_token(paths, SecretRedactor())

    def test_duplicate_existing_service_tokens_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = cli.build_parser(env).parse_args([])
            paths = cli.build_paths(args, env)
            paths.mcp_config_dir.mkdir(parents=True)
            paths.mcp_auth_token_file.write_text(GPT_ACTIONS_TOKEN + "\n", encoding="utf-8")
            paths.gpt_actions_token_file.write_text(
                GPT_ACTIONS_TOKEN + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ToolError, "must be different"):
                credentials.resolve_service_tokens(paths, SecretRedactor())

    def test_service_token_report_contains_only_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = cli.build_parser(env).parse_args([])
            paths = cli.build_paths(args, env)
            with self.assertLogs("joplin-terminal-installer", level="INFO") as captured:
                credentials.report_service_token_paths(paths)
            logs = "\n".join(captured.output)
            self.assertIn(str(paths.gpt_actions_token_file), logs)
            self.assertIn(str(paths.mcp_auth_token_file), logs)
            self.assertNotIn(GPT_ACTIONS_TOKEN, logs)
            self.assertNotIn(MCP_TOKEN, logs)

    def test_gpt_actions_token_matches_runtime_encoding_requirement(self) -> None:
        credentials.validate_gpt_actions_token(GPT_ACTIONS_TOKEN)
        with self.assertRaisesRegex(ToolError, "32 bytes"):
            credentials.validate_gpt_actions_token("g" * 36)
        with self.assertRaisesRegex(ToolError, "URL-safe Base64"):
            credentials.validate_gpt_actions_token("not valid base64!")


class PurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "PATH": os.environ["PATH"],
        }
        self.args = cli.build_parser(self.env).parse_args(
            [
                "--purge",
                "--yes",
                "--non-interactive",
                "--joplin-prefix",
                str(self.home / ".local"),
            ]
        )
        self.paths = cli.build_paths(self.args, self.env)

    @staticmethod
    def stopped_runner() -> mock.Mock:
        runner = mock.Mock()
        runner.run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 3, "inactive\n", ""),
            subprocess.CompletedProcess([], 3, "inactive\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        return runner

    def populate(self) -> tuple[Path, Path]:
        for directory in (
            self.paths.npm_prefix,
            self.paths.profile_dir,
            self.paths.config_dir,
            self.paths.mcp_config_dir,
            self.paths.state_dir,
            self.paths.deploy_dir,
            self.paths.launcher.parent,
            self.paths.unit_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.paths.npm_prefix / "package.json").write_text("{}\n")
        (self.paths.profile_dir / "database.sqlite").write_text("profile\n")
        self.paths.token_file.write_text("api-token\n")
        self.paths.mcp_auth_token_file.write_text("mcp-token\n")
        self.paths.gpt_actions_token_file.write_text("gpt-actions-token\n")
        (self.paths.deploy_dir / "run.py").write_text("pass\n")
        self.paths.mcp_binary.write_text("binary\n")
        releases.record_mcp_receipt(self.paths)
        self.paths.launcher.symlink_to(self.paths.npm_prefix / "bin" / "joplin")
        self.paths.unit_path.write_text("unit\n")
        self.paths.adapter_unit_path.write_text("adapter unit\n")
        self.paths.unit_path.with_name(f"{common.SERVICE_NAME}.bak-test").write_text("backup\n")
        self.paths.adapter_unit_path.with_name(
            f"{common.ADAPTER_SERVICE_NAME}.bak-test"
        ).write_text("backup\n")
        unrelated_bin = self.paths.launcher.parent / "keep-me"
        unrelated_bin.write_text("keep\n")
        unrelated_unit = self.paths.unit_path.parent / "keep-me.service"
        unrelated_unit.write_text("keep\n")
        return unrelated_bin, unrelated_unit

    def test_full_purge_removes_only_managed_local_paths_and_is_idempotent(self) -> None:
        unrelated_bin, unrelated_unit = self.populate()
        runner = self.stopped_runner()
        managed.purge_installation(
            runner,
            Path("/systemctl"),
            self.paths,
            self.env,
        )
        for path in (
            self.paths.launcher,
            self.paths.mcp_binary,
            self.paths.npm_prefix,
            self.paths.profile_dir,
            self.paths.config_dir,
            self.paths.mcp_config_dir,
            self.paths.state_dir,
            self.paths.deploy_dir,
            self.paths.unit_path,
            self.paths.adapter_unit_path,
        ):
            self.assertFalse(path.exists() or path.is_symlink(), path)
        self.assertTrue(unrelated_bin.is_file())
        self.assertTrue(unrelated_unit.is_file())

        managed.purge_installation(
            self.stopped_runner(),
            Path("/systemctl"),
            self.paths,
            self.env,
        )

    def test_purge_removes_the_adapter_only_when_it_matches_the_installer_receipt(self) -> None:
        operator_file = self.root / "operator-joplin-md-sync"
        for case in ("pre-receipt", "replaced", "symlink", "other path"):
            with self.subTest(case=case):
                self.populate()
                binary = self.paths.mcp_binary
                if case == "pre-receipt":
                    self.paths.mcp_receipt_file.unlink()
                elif case == "replaced":
                    binary.write_text("operator build\n")
                elif case == "symlink":
                    operator_file.write_text("operator build\n")
                    binary.unlink()
                    binary.symlink_to(operator_file)
                else:
                    receipt = json.loads(self.paths.mcp_receipt_file.read_text())
                    receipt["path"] = str(binary.with_name("elsewhere"))
                    self.paths.mcp_receipt_file.write_text(json.dumps(receipt) + "\n")
                before = binary.read_bytes()
                with self.assertLogs("joplin-terminal-installer", logging.INFO) as logs:
                    operations.dry_run_purge(self.paths, self.env)
                    managed.purge_installation(
                        self.stopped_runner(), Path("/systemctl"), self.paths, self.env
                    )
                self.assertTrue(binary.exists() or binary.is_symlink())
                self.assertEqual(binary.read_bytes(), before)
                self.assertEqual(binary.is_symlink(), case == "symlink")
                messages = "\n".join(logs.output)
                self.assertIn(f"would preserve unrecorded executable {binary}", messages)
                self.assertIn(f"preserving {binary}", messages)
                self.assertFalse(self.paths.profile_dir.exists())
                self.assertFalse(self.paths.state_dir.exists())
                binary.unlink()
        self.assertEqual(operator_file.read_text(), "operator build\n")
        self.populate()
        managed.purge_installation(self.stopped_runner(), Path("/systemctl"), self.paths, self.env)
        self.assertFalse(self.paths.mcp_binary.exists())

    def test_profile_lock_blocks_purge_before_data_removal(self) -> None:
        self.populate()
        lock = ProfileLock(self.paths.lock_file)
        lock.acquire()
        self.addCleanup(lock.release)
        with self.assertRaisesRegex(ToolError, "profile is already in use"):
            managed.purge_installation(
                self.stopped_runner(),
                Path("/systemctl"),
                self.paths,
                self.env,
            )
        self.assertTrue((self.paths.profile_dir / "database.sqlite").is_file())

    def test_noninteractive_clean_needs_no_yes_and_never_prompts(self) -> None:
        self.args.yes = False
        prompt = mock.Mock(side_effect=AssertionError("clean must not prompt"))
        tool = application.Installer(self.args, env=self.env, input_fn=prompt, getpass_fn=prompt)
        with (
            mock.patch.object(application, "purge_systemctl_path", return_value=Path("/systemctl")),
            mock.patch.object(application, "purge_installation") as purge,
            mock.patch.object(application, "check_dependencies") as dependencies,
        ):
            tool.run()
        purge.assert_called_once()
        dependencies.assert_not_called()
        prompt.assert_not_called()

    def test_clean_alias_is_prompt_free_in_interactive_mode(self) -> None:
        self.args.yes = False
        self.args.non_interactive = False
        parsed = cli.build_parser(self.env).parse_args(["--clean"])
        self.assertTrue(parsed.purge)
        prompt = mock.Mock(side_effect=AssertionError("clean must not prompt"))
        tool = application.Installer(parsed, env=self.env, input_fn=prompt, getpass_fn=prompt)
        with (
            mock.patch.object(application, "purge_systemctl_path", return_value=Path("/systemctl")),
            mock.patch.object(application, "purge_installation") as purge,
        ):
            tool.run()
        purge.assert_called_once()
        prompt.assert_not_called()

    def test_purge_dry_run_does_not_require_systemctl_or_delete_data(self) -> None:
        self.populate()
        self.args.dry_run = True
        tool = application.Installer(
            self.args,
            env=self.env,
            input_fn=mock.Mock(side_effect=AssertionError("must not prompt")),
        )
        write_installed_units(tool)
        with mock.patch.object(
            application,
            "purge_systemctl_path",
            side_effect=AssertionError("must not check systemctl"),
        ):
            tool.run()
        self.assertTrue((self.paths.profile_dir / "database.sqlite").is_file())


class UnitAndPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        env = {
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"),
        }
        self.args = cli.build_parser(env).parse_args(
            [
                "--sync-target",
                "nextcloud",
                "--sync-location",
                "https://cloud.invalid/Joplin",
                "--sync-username",
                "user",
                "--joplin-prefix",
                str(self.root / "prefix"),
            ]
        )
        self.paths = cli.build_paths(self.args, env)

    def test_isolated_layout(self) -> None:
        self.assertEqual(
            self.paths.npm_prefix,
            self.root / "prefix" / "share" / "joplin-agent" / "npm",
        )
        self.assertEqual(self.paths.launcher, self.root / "prefix" / "bin" / "joplin")
        self.assertEqual(
            self.paths.mcp_binary,
            self.root / "prefix" / "bin" / "joplin-md-sync",
        )
        self.assertEqual(
            self.paths.mcp_auth_token_file,
            self.root / "config" / "joplin-md-sync" / "mcp-token",
        )
        self.assertEqual(
            self.paths.gpt_actions_token_file,
            self.root / "config" / "joplin-md-sync" / "gpt-actions-token",
        )

    def test_rendered_unit_has_no_secrets_and_absolute_paths(self) -> None:
        template = (TOOLS_DIR / "systemd" / "joplin-terminal.service").read_text()
        content = service.render_unit(
            template,
            python=Path(sys.executable).resolve(),
            node=Path(sys.executable).resolve(),
            supervisor=(self.root / "deployed" / "runner.py").resolve(),
            paths=self.paths,
            api_port=41185,
            sync_interval=300,
        )
        self.assertIn("--api-port", content)
        self.assertIn("41185", content)
        self.assertIn("KillMode=mixed", content)
        self.assertIn("UMask=0077", content)
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn(
            f'ReadWritePaths="{self.paths.profile_dir}" "{self.paths.lock_file.parent}"',
            content,
        )
        self.assertNotIn("nextcloud-secret", content)
        self.assertNotIn("e2ee-secret", content)
        self.assertNotIn("api-token", content)

        mcp_content = service.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
        )
        self.assertIn('"--port" "41185"', mcp_content)
        self.assertIn('"--mcp-port" "8765"', mcp_content)
        self.assertIn(str(self.paths.token_file), mcp_content)
        self.assertIn('"--gpt-actions"', mcp_content)
        self.assertIn('"--gpt-actions-token-file"', mcp_content)
        self.assertIn(str(self.paths.gpt_actions_token_file), mcp_content)
        self.assertIn('"--auth-token-file"', mcp_content)
        self.assertIn(str(self.paths.mcp_auth_token_file), mcp_content)
        self.assertIn("Requires=joplin-terminal.service", mcp_content)
        self.assertNotIn("a" * 32, mcp_content)

        remote = service.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
            allow_remote=True,
        )
        self.assertIn('"--host" "0.0.0.0"', remote)
        self.assertIn('"--allow-remote-mcp"', remote)
        self.assertIn('"--auth-token-file"', remote)

    def test_snap_node_unit_avoids_mount_namespace_hardening(self) -> None:
        snap_dispatcher = self.root / "snap"
        snap_dispatcher.write_text("dispatcher\n")
        node_alias = self.root / "node"
        node_alias.symlink_to(snap_dispatcher)
        content = service.render_unit(
            (TOOLS_DIR / "systemd" / "joplin-terminal.service").read_text(),
            python=Path(sys.executable).resolve(),
            node=node_alias,
            supervisor=(self.root / "deployed" / "runner.py").resolve(),
            paths=self.paths,
            api_port=41185,
            sync_interval=300,
        )
        self.assertIn(f'"--node-path" "{node_alias}"', content)
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn("PrivateDevices=false", content)
        self.assertIn("PrivateTmp=false", content)
        self.assertIn("ProtectControlGroups=false", content)
        self.assertIn("ProtectKernelModules=false", content)
        self.assertIn("ProtectKernelTunables=false", content)
        self.assertIn("ProtectSystem=false", content)
        self.assertNotIn("ReadWritePaths=", content)

    def test_regular_node_unit_keeps_mount_namespace_hardening(self) -> None:
        node = self.root / "node"
        node.write_text("node\n")
        content = service.render_unit(
            (TOOLS_DIR / "systemd" / "joplin-terminal.service").read_text(),
            python=Path(sys.executable).resolve(),
            node=node,
            supervisor=(self.root / "deployed" / "runner.py").resolve(),
            paths=self.paths,
            api_port=41185,
            sync_interval=300,
        )
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn("PrivateDevices=true", content)
        self.assertIn("PrivateTmp=true", content)
        self.assertIn("ProtectControlGroups=true", content)
        self.assertIn("ProtectKernelModules=true", content)
        self.assertIn("ProtectKernelTunables=true", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ReadWritePaths=", content)

    def test_filesystem_target_is_writable_inside_service_sandbox(self) -> None:
        sync_directory = self.root / "mounted notes"
        sync_directory.mkdir()
        content = service.render_unit(
            (TOOLS_DIR / "systemd" / "joplin-terminal.service").read_text(),
            python=Path(sys.executable).resolve(),
            node=Path(sys.executable).resolve(),
            supervisor=(self.root / "deployed" / "runner.py").resolve(),
            paths=self.paths,
            api_port=41185,
            sync_interval=300,
            extra_write_paths=(sync_directory,),
        )
        read_write_line = next(
            line for line in content.splitlines() if line.startswith("ReadWritePaths=")
        )
        self.assertIn(f'"{sync_directory}"', read_write_line)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_rendered_unit_passes_systemd_analyze(self) -> None:
        supervisor = (self.root / "deployed" / "runner.py").resolve()
        supervisor.parent.mkdir(parents=True)
        supervisor.write_text("pass\n")
        supervisor.chmod(0o700)
        content = service.render_unit(
            (TOOLS_DIR / "systemd" / "joplin-terminal.service").read_text(),
            python=Path(sys.executable).resolve(),
            node=Path(sys.executable).resolve(),
            supervisor=supervisor,
            paths=self.paths,
            api_port=41185,
            sync_interval=300,
        )
        unit = self.root / "joplin-terminal.service"
        unit.write_text(content)
        result = subprocess.run(
            ["systemd-analyze", "verify", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.paths.mcp_binary.parent.mkdir(parents=True, exist_ok=True)
        self.paths.mcp_binary.write_text("standalone\n")
        self.paths.mcp_binary.chmod(0o700)
        adapter_unit = self.root / "joplin-md-sync.service"
        common.atomic_write(
            adapter_unit,
            service.render_adapter_unit(
                (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
                paths=self.paths,
                api_port=41185,
                mcp_port=8765,
            ).encode(),
            0o644,
        )
        result = subprocess.run(
            ["systemd-analyze", "verify", adapter_unit, unit],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_unit_gets_backup(self) -> None:
        supervisor = self.root / "runner.py"
        supervisor.write_text("pass\n")
        self.paths.unit_path.parent.mkdir(parents=True)
        self.paths.unit_path.write_text("old unit\n")
        changed = service.install_unit(
            self.paths,
            self.args,
            supervisor,
            Path(sys.executable).resolve(),
        )
        self.assertTrue(changed)
        backups = list(self.paths.unit_path.parent.glob("joplin-terminal.service.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old unit\n")
        self.assertEqual(stat.S_IMODE(self.paths.unit_path.stat().st_mode), 0o644)

    def test_profile_lock_is_exclusive(self) -> None:
        first = ProfileLock(self.paths.lock_file)
        second = ProfileLock(self.paths.lock_file)
        first.acquire()
        self.addCleanup(first.release)
        with self.assertRaisesRegex(ToolError, "already in use"):
            second.acquire()

    def test_api_token_is_protected_and_redacted(self) -> None:
        token = "a" * 32
        redactor = SecretRedactor()
        with mock.patch.object(sync, "read_setting", return_value=token):
            sync.extract_api_token(mock.Mock(), self.paths, redactor)
        self.assertEqual(self.paths.token_file.read_text(), f"{token}\n")
        self.assertEqual(stat.S_IMODE(self.paths.token_file.stat().st_mode), 0o600)
        self.assertNotIn(token, redactor.redact(token))

    def test_generated_mcp_auth_token_is_stored_protected(self) -> None:
        redactor = SecretRedactor()
        token = MCP_TOKEN
        credentials.configure_mcp_auth_token(self.paths, token)
        self.assertEqual(
            credentials.load_mcp_auth_token(self.paths, redactor),
            token,
        )
        self.assertEqual(
            stat.S_IMODE(self.paths.mcp_auth_token_file.stat().st_mode),
            0o600,
        )
        unit = service.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
        )
        self.assertNotIn(token, unit)

    def test_gpt_actions_token_is_stored_protected_and_must_be_distinct(self) -> None:
        token = GPT_ACTIONS_TOKEN
        credentials.configure_gpt_actions_token(self.paths, token)
        self.assertEqual(
            credentials.load_gpt_actions_token(self.paths, SecretRedactor()),
            token,
        )
        self.assertEqual(
            stat.S_IMODE(self.paths.gpt_actions_token_file.stat().st_mode),
            0o600,
        )
        self.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.token_file.write_text("j" * 32)
        credentials.validate_distinct_service_tokens(self.paths, token, MCP_TOKEN)
        with self.assertRaisesRegex(ToolError, "MCP bearer tokens must be different"):
            credentials.validate_distinct_service_tokens(self.paths, token, token)
        self.paths.token_file.write_text(token)
        with self.assertRaisesRegex(ToolError, "Joplin API tokens must be different"):
            credentials.validate_distinct_service_tokens(self.paths, token, MCP_TOKEN)
        self.paths.token_file.write_text(MCP_TOKEN)
        with self.assertRaisesRegex(ToolError, "MCP and Joplin API tokens"):
            credentials.validate_distinct_service_tokens(
                self.paths,
                GPT_ACTIONS_TOKEN,
                MCP_TOKEN,
            )

    def test_non_ascii_joplin_token_does_not_crash_token_comparison(self) -> None:
        self.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.token_file.write_text("t\u00f6ken\n", encoding="utf-8")

        credentials.validate_distinct_service_tokens(
            self.paths,
            GPT_ACTIONS_TOKEN,
            MCP_TOKEN,
        )


class ConfigurationTests(unittest.TestCase):
    def test_sync_rejects_zero_exit_with_error_or_incomplete_report_and_restores_locale(
        self,
    ) -> None:
        for report in (
            "Completed: now. Last error: synthetic private error",
            "Sync target must be upgraded",
            "",
        ):
            runner = mock.Mock()
            runner.run_long.return_value = subprocess.CompletedProcess([], 0, report, "")
            with (
                mock.patch.object(sync, "read_setting", return_value="ru_RU"),
                mock.patch.object(sync, "write_setting") as write,
                self.assertRaisesRegex(ToolError, "did not complete"),
            ):
                sync.run_sync(runner, mock.Mock(), "initial sync")
            self.assertEqual(
                [call.args[2:] for call in write.call_args_list],
                [("locale", "en_GB"), ("locale", "ru_RU")],
            )

    def test_default_target_with_choice_description_is_not_a_conflict(self) -> None:
        args = mock.Mock(
            sync_interval=300,
            api_port=41185,
            force_reconfigure=False,
            non_interactive=True,
        )
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["nextcloud"],
            (
                models.SyncSetting("sync.5.path", "https://new.invalid/Joplin"),
                models.SyncSetting("sync.5.username", "user"),
                models.SyncSetting("sync.5.password", "password", secret=True),
            ),
        )
        paths = mock.Mock(profile_dir=Path("/profile"))
        runner = mock.Mock()
        with (
            mock.patch.object(
                sync,
                "read_setting",
                side_effect=[
                    "0 (0: (None), 5: Nextcloud, 6: WebDAV)",
                    "null",
                    "null",
                ],
            ),
            mock.patch.object(sync, "write_setting") as write,
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "chmod"),
        ):
            sync.configure_profile(runner, paths, args, configuration)
        writes = [(call.args[2], call.args[3]) for call in write.call_args_list]
        self.assertEqual(
            writes,
            [
                ("sync.5.path", "https://new.invalid/Joplin"),
                ("sync.5.username", "user"),
                ("sync.5.password", "password"),
                ("sync.target", "5"),
                ("sync.interval", "300"),
                ("api.port", "41185"),
            ],
        )

    def test_force_reconfigure(self) -> None:
        args = mock.Mock(force_reconfigure=True, non_interactive=True)
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["nextcloud"],
            (models.SyncSetting("sync.5.path", "https://new.invalid/Joplin"),),
        )
        sync.confirm_reconfigure(
            args,
            configuration,
            "6",
            {"sync.5.path": "https://old.invalid/Joplin"},
        )

    def test_new_profile_ignores_driver_defaults_during_conflict_check(self) -> None:
        args = mock.Mock(force_reconfigure=False, non_interactive=True)
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["s3"],
            (models.SyncSetting("sync.8.forcePathStyle", "false"),),
        )
        sync.confirm_reconfigure(
            args,
            configuration,
            "0",
            {"sync.8.forcePathStyle": "0"},
            input_fn=mock.Mock(side_effect=AssertionError("must not prompt")),
        )

    def test_noninteractive_conflict_requires_force(self) -> None:
        args = mock.Mock(force_reconfigure=False, non_interactive=True)
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["nextcloud"],
            (models.SyncSetting("sync.5.path", "https://new.invalid/Joplin"),),
        )
        with self.assertRaisesRegex(ToolError, "--force-reconfigure"):
            sync.confirm_reconfigure(
                args,
                configuration,
                "5",
                {"sync.5.path": "https://old.invalid/Joplin"},
            )

    def test_interactive_conflict_can_be_rejected(self) -> None:
        args = mock.Mock(force_reconfigure=False, non_interactive=False)
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["nextcloud"],
            (models.SyncSetting("sync.5.path", "https://new.invalid/Joplin"),),
        )
        with self.assertRaisesRegex(ToolError, "cancelled"):
            sync.confirm_reconfigure(
                args,
                configuration,
                "5",
                {"sync.5.path": "https://old.invalid/Joplin"},
                input_fn=lambda _prompt: "n",
            )

    def test_each_static_target_maps_to_joplin_settings(self) -> None:
        cases = {
            "filesystem": (
                ["--sync-location", "/tmp"],
                {"sync.2.path": "/tmp"},
            ),
            "nextcloud": (
                [
                    "--sync-location",
                    "https://cloud.invalid/Joplin",
                    "--sync-username",
                    "user",
                ],
                {
                    "sync.5.path": "https://cloud.invalid/Joplin",
                    "sync.5.username": "user",
                    "sync.5.password": "sync-secret",
                },
            ),
            "webdav": (
                ["--sync-location", "https://dav.invalid/Joplin"],
                {
                    "sync.6.path": "https://dav.invalid/Joplin",
                    "sync.6.username": "",
                    "sync.6.password": "",
                },
            ),
            "joplin-server": (
                [
                    "--sync-location",
                    "https://server.invalid",
                    "--sync-username",
                    "user@example.com",
                ],
                {
                    "sync.9.path": "https://server.invalid",
                    "sync.9.username": "user@example.com",
                    "sync.9.password": "sync-secret",
                },
            ),
            "joplin-server-saml": (
                ["--sync-location", "https://server.invalid"],
                {"sync.11.path": "https://server.invalid"},
            ),
        }
        for target, (extra, expected) in cases.items():
            args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
                ["--sync-target", target, *extra]
            )
            cli.validate_args(args)
            configuration = sync.resolve_sync_configuration(
                args,
                SecretRedactor(),
                getpass_fn=lambda _prompt: "sync-secret",
            )
            with self.subTest(target=target):
                self.assertEqual(
                    {setting.name: setting.value for setting in configuration.settings},
                    expected,
                )

    def test_s3_maps_every_driver_setting(self) -> None:
        args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(
            [
                "--sync-target",
                "s3",
                "--sync-location",
                "notes-bucket",
                "--sync-username",
                "access-key",
                "--s3-endpoint",
                "https://s3.invalid/storage",
                "--s3-region",
                "eu-north-1",
                "--s3-force-path-style",
            ]
        )
        cli.validate_args(args)
        configuration = sync.resolve_sync_configuration(
            args,
            SecretRedactor(),
            getpass_fn=lambda _prompt: "secret-key",
        )
        self.assertEqual(
            {setting.name: setting.value for setting in configuration.settings},
            {
                "sync.8.path": "notes-bucket",
                "sync.8.url": "https://s3.invalid/storage/",
                "sync.8.region": "eu-north-1",
                "sync.8.username": "access-key",
                "sync.8.password": "secret-key",
                "sync.8.forcePathStyle": "true",
            },
        )

    def test_browser_targets_have_no_static_settings(self) -> None:
        for target in ("onedrive", "dropbox", "joplin-cloud"):
            args = cli.build_parser({"HOME": "/tmp/home"}).parse_args(["--sync-target", target])
            cli.validate_args(args)
            configuration = sync.resolve_sync_configuration(args, SecretRedactor())
            with self.subTest(target=target):
                self.assertEqual(configuration.settings, ())

    def test_browser_sync_requires_persisted_auth_before_verification(self) -> None:
        paths = mock.Mock(launcher=Path("/joplin"), profile_dir=Path("/profile"))
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "sync.7.auth = null\n", "")
        with self.assertRaisesRegex(ToolError, "authentication was not completed"):
            sync.run_browser_authenticated_sync(
                runner,
                paths,
                models.SYNC_TARGETS["dropbox"],
            )
        runner.run_interactive.assert_called_once()
        runner.run_long.assert_not_called()

    def test_saml_code_exchange_validates_and_returns_session(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                self.close()

        opened: list[str] = []

        def urlopen(request: urllib.request.Request, *, timeout: float) -> Response:
            opened.append(request.full_url)
            self.assertEqual(timeout, 30.0)
            return Response(b'{"id":"session-token","user_id":"user-id"}')

        self.assertEqual(
            sync.exchange_saml_code(
                "https://server.invalid",
                "123-456-789",
                urlopen=urlopen,
            ),
            ("session-token", "user-id"),
        )
        self.assertEqual(
            opened,
            ["https://server.invalid/api/login_with_code/123456789"],
        )
        with self.assertRaisesRegex(ToolError, "exactly 9 digits"):
            sync.exchange_saml_code(
                "https://server.invalid",
                "not-a-code",
                urlopen=urlopen,
            )

    def test_saml_rerun_reuses_existing_session(self) -> None:
        configuration = models.SyncConfiguration(
            models.SYNC_TARGETS["joplin-server-saml"],
            (models.SyncSetting("sync.11.path", "https://server.invalid"),),
        )
        with (
            mock.patch.object(
                sync,
                "read_setting",
                side_effect=["existing-session", "existing-user"],
            ),
            mock.patch.object(sync, "write_setting") as write,
        ):
            sync.authenticate_saml(
                mock.Mock(),
                mock.Mock(),
                configuration,
                SecretRedactor(),
                reuse_existing=True,
                input_fn=mock.Mock(side_effect=AssertionError("must not prompt")),
            )
        write.assert_not_called()

    def test_unencrypted_remote_skips_e2ee_password(self) -> None:
        with (
            mock.patch.object(encryption, "e2ee_status_enabled", return_value=False),
            mock.patch.object(
                encryption,
                "resolve_secret",
                side_effect=AssertionError("must not request E2EE password"),
            ),
        ):
            enabled = encryption.bootstrap_e2ee(
                mock.Mock(),
                mock.Mock(),
                mock.Mock(),
                SecretRedactor(),
            )
        self.assertFalse(enabled)


class LingeringTests(unittest.TestCase):
    def test_enabled_lingering_does_not_prompt(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "yes\n", "")
        prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
        args = mock.Mock(enable_linger=False, non_interactive=False)
        with mock.patch.object(managed, "current_username", return_value="agent"):
            self.assertTrue(
                managed.configure_lingering(
                    runner,
                    Path("/loginctl"),
                    args,
                    input_fn=prompt,
                )
            )
        prompt.assert_not_called()

    def test_interactive_lingering_offer_enables_and_verifies(self) -> None:
        runner = mock.Mock()
        runner.run.side_effect = [
            subprocess.CompletedProcess([], 0, "no\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "yes\n", ""),
        ]
        args = mock.Mock(enable_linger=False, non_interactive=False)
        with mock.patch.object(managed, "current_username", return_value="agent"):
            self.assertTrue(
                managed.configure_lingering(
                    runner,
                    Path("/loginctl"),
                    args,
                    input_fn=lambda _prompt: "yes",
                )
            )
        self.assertEqual(
            runner.run.call_args_list[1].args[0],
            [Path("/loginctl"), "enable-linger", "agent"],
        )

    def test_noninteractive_lingering_requires_explicit_flag(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "no\n", "")
        args = mock.Mock(enable_linger=False, non_interactive=True)
        with mock.patch.object(managed, "current_username", return_value="agent"):
            self.assertFalse(managed.configure_lingering(runner, Path("/loginctl"), args))
        self.assertEqual(runner.run.call_count, 1)

    def test_enable_linger_flag_works_without_prompt(self) -> None:
        runner = mock.Mock()
        runner.run.side_effect = [
            subprocess.CompletedProcess([], 0, "no\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "yes\n", ""),
        ]
        args = mock.Mock(enable_linger=True, non_interactive=True)
        prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
        with mock.patch.object(managed, "current_username", return_value="agent"):
            self.assertTrue(
                managed.configure_lingering(
                    runner,
                    Path("/loginctl"),
                    args,
                    input_fn=prompt,
                )
            )
        prompt.assert_not_called()


class DryRunTests(unittest.TestCase):
    def test_dry_run_creates_nothing_and_does_not_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--sync-target",
                    "nextcloud",
                    "--sync-location",
                    "https://cloud.invalid/Joplin",
                    "--sync-username",
                    "user",
                    "--dry-run",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
            redactor = SecretRedactor()
            tool = application.Installer(
                args,
                env=env,
                getpass_fn=prompt,
                redactor=redactor,
            )
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with mock.patch.object(application, "check_dependencies", return_value=dependencies):
                tool.run()
            self.assertFalse((root / "prefix").exists())
            self.assertFalse((root / "profile").exists())
            prompt.assert_not_called()

    def test_upgrade_updates_both_without_resolving_sync_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            joplin_version = "3.6.2"
            mcp_version = "1.2.0"
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    joplin_version,
                    "--joplin-md-sync-version",
                    mcp_version,
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                    "--no-start-service",
                ]
            )
            prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
            tool = application.Installer(args, env=env, getpass_fn=prompt)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=False),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(
                    application, "install_or_update_joplin", return_value=joplin_version
                ) as update_joplin,
                mock.patch.object(application, "installed_mcp_version", return_value=mcp_version),
                mock.patch.object(
                    application, "install_or_update_mcp", return_value=mcp_version
                ) as update_mcp,
                mock.patch.object(application, "configure_profile") as configure,
                mock.patch.object(application, "bootstrap_e2ee") as bootstrap,
            ):
                tool.run()
            update_joplin.assert_called_once_with(
                tool.runner,
                dependencies,
                tool.paths,
                joplin_version,
            )
            update_mcp.assert_called_once_with(tool.runner, tool.paths, mcp_version)
            configure.assert_not_called()
            bootstrap.assert_not_called()
            prompt.assert_not_called()
            self.assertFalse((root / "profile").exists())


class ServiceLifecycleTests(unittest.TestCase):
    def test_upgrade_preserves_custom_units_deploys_helpers_and_keeps_old_files(self) -> None:
        for old_snap in (False, True):
            with self.subTest(old_snap=old_snap):
                self._upgrade_preservation_case(old_snap)

    def _upgrade_preservation_case(self, old_snap: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            filesystem = root / "sync files %"
            filesystem.mkdir()
            original = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--api-port",
                    "49151",
                    "--mcp-port",
                    "49152",
                    "--sync-interval",
                    "600",
                    "--allow-remote-mcp",
                    "--joplin-prefix",
                    str(root / "custom prefix"),
                    "--profile-dir",
                    str(root / "custom profile"),
                    "--sync-target",
                    "filesystem",
                    "--sync-location",
                    str(filesystem),
                ]
            )
            old = application.Installer(original, env=env)
            write_installed_units(old)
            if old_snap:
                old.paths.unit_path.write_text(
                    "\n".join(
                        line
                        for line in old.paths.unit_path.read_text().splitlines()
                        if not line.startswith("ReadWritePaths=")
                    )
                    + "\n"
                )
            old.paths.deploy_dir.mkdir(parents=True)
            (old.paths.deploy_dir / "run_joplin_terminal.py").write_text("old supervisor")
            old.paths.mcp_binary.parent.mkdir(parents=True, exist_ok=True)
            old.paths.mcp_binary.write_text("old executable")
            old.paths.npm_prefix.mkdir(parents=True)
            (old.paths.npm_prefix / "old-package").write_text("recoverable package")
            old.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            old.paths.token_file.write_text("j" * 32)
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--no-start-service",
                    "--joplin-version",
                    "3.7.1",
                    "--joplin-md-sync-version",
                    "9.9.1",
                ]
            )
            tool = application.Installer(args, env=env)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22", "10"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=False),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "installed_joplin_version", return_value="3.6.2"),
                mock.patch.object(
                    application,
                    "read_setting",
                    side_effect=lambda _runner, _paths, name: (
                        "2 (File system)" if name == "sync.target" else str(filesystem)
                    ),
                ),
                mock.patch.object(application, "install_or_update_joplin", return_value="3.7.1"),
                mock.patch.object(application, "installed_mcp_version", return_value="9.9.1"),
                mock.patch.object(application, "install_or_update_mcp", return_value="9.9.1"),
                mock.patch.object(
                    sync, "resolve_secret", side_effect=AssertionError("no secret prompt")
                ),
            ):
                tool.run()
            self.assertEqual(tool.paths.prefix, old.paths.prefix)
            self.assertEqual(tool.paths.profile_dir, old.paths.profile_dir)
            self.assertEqual(args.api_port, 49151)
            self.assertEqual(args.mcp_port, 49152)
            self.assertEqual(args.sync_interval, 600)
            self.assertTrue(args.allow_remote_mcp)
            self.assertIn("0.0.0.0", tool.paths.adapter_unit_path.read_text())
            self.assertIn(str(filesystem).replace("%", "%%"), tool.paths.unit_path.read_text())
            self.assertEqual(
                (tool.paths.deploy_dir / "run_joplin_terminal.py").read_bytes(),
                (TOOLS_DIR / "run_joplin_terminal.py").read_bytes(),
            )
            backups = list(tool.paths.state_dir.glob("upgrade-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "joplin-md-sync").read_text(), "old executable")
            self.assertEqual(
                (backups[0] / "npm" / "old-package").read_text(), "recoverable package"
            )
            self.assertEqual(
                (backups[0] / "joplin-terminal-service" / "run_joplin_terminal.py").read_text(),
                "old supervisor",
            )

    def test_upgrade_explicit_options_override_units_and_invalid_units_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"HOME": str(root), "JOPLIN_API_PORT": "49155"}
            args = cli.build_parser(env).parse_args(
                ["--upgrade", "--api-port", "49151", "--allow-remote-mcp"]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            args = cli.build_parser(env).parse_args(
                ["--upgrade", "--no-allow-remote-mcp", "--mcp-port", "49156"]
            )
            tool = application.Installer(args, env=env)
            with mock.patch.object(
                application,
                "check_dependencies",
                side_effect=ToolError("after argument restoration"),
            ):
                with self.assertRaisesRegex(ToolError, "after argument restoration"):
                    tool.run()
            self.assertEqual(args.api_port, 49155)
            self.assertEqual(args.mcp_port, 49156)
            self.assertIn("api_port", args.explicit_options)
            self.assertIn("allow_remote_mcp", args.explicit_options)
            self.assertFalse(args.allow_remote_mcp)
            tool.paths.adapter_unit_path.write_text("[Service]\nExecStart=unknown\n")
            with mock.patch.object(application, "check_dependencies") as dependencies:
                with self.assertRaises(ToolError):
                    tool.run()
            dependencies.assert_not_called()

    def test_invalid_oauth_value_or_undecodable_unit_is_a_clean_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"HOME": str(root)}
            tool = application.Installer(cli.build_parser(env).parse_args([]), env=env)
            write_installed_units(tool)
            adapter = tool.paths.adapter_unit_path
            adapter.write_text(
                adapter.read_text().replace(
                    '"--auth-token-file"',
                    '"--github-oauth" "--github-oauth-access-token-ttl" "soon" "--auth-token-file"',
                )
            )
            for message in (
                "invalid --github-oauth-access-token-ttl",
                "cannot read installed unit",
            ):
                with (
                    self.subTest(message=message),
                    mock.patch.object(application, "check_dependencies") as dependencies,
                    self.assertRaisesRegex(ToolError, message),
                ):
                    application.Installer(cli.build_parser(env).parse_args([]), env=env).run()
                dependencies.assert_not_called()
                adapter.write_bytes(b"[Service]\nExecStart=\xff\n")

    def test_failed_adapter_download_keeps_healthy_services_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    "3.6.2",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=True),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "installed_mcp_version", return_value="1.1.0"),
                mock.patch.object(
                    application,
                    "download_mcp_binary",
                    side_effect=ToolError("could not download release asset fixture"),
                ),
                mock.patch.object(application, "systemctl_command") as systemctl,
                mock.patch.object(application, "stop_after_startup_failure") as stop_failed,
                mock.patch.object(application, "backup_upgrade_files") as backup,
                mock.patch.object(application, "install_or_update_joplin") as update_joplin,
                self.assertRaisesRegex(ToolError, "could not download"),
            ):
                tool.run()
            # Without OAuth, the candidate is still verified before any stop.
            systemctl.assert_not_called()
            stop_failed.assert_not_called()
            backup.assert_not_called()
            update_joplin.assert_not_called()

    def test_failed_joplin_upgrade_restores_previous_tree_and_restarts_active_services(
        self,
    ) -> None:
        for active in (
            {"joplin-terminal.service", "joplin-md-sync.service"},
            {"joplin-terminal.service"},
            set(),
        ):
            with self.subTest(active=sorted(active)):
                self._failed_joplin_upgrade_case(active)
        with self.subTest(restore_fails=True):
            self._failed_joplin_upgrade_case(
                {"joplin-terminal.service", "joplin-md-sync.service"}, restore_fails=True
            )

    def test_upgrade_records_the_replaced_adapter_for_purge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--no-start-service",
                    "--joplin-version",
                    "3.6.2",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            tool.paths.mcp_binary.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.mcp_binary.write_bytes(b"pre-receipt adapter")
            self.assertFalse(managed.owned_mcp_binary(tool.paths))
            prepared = tool.paths.mcp_binary.with_name(".joplin-md-sync.prepared")
            prepared.write_bytes(b"verified adapter 1.2.0")
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=False),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "installed_mcp_version", return_value="1.1.0"),
                mock.patch.object(application, "download_mcp_binary", return_value=prepared),
                mock.patch.object(application, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(application, "systemctl_command"),
            ):
                tool.run()
            self.assertEqual(tool.paths.mcp_binary.read_bytes(), b"verified adapter 1.2.0")
            self.assertTrue(managed.owned_mcp_binary(tool.paths))
            self.assertEqual(stat.S_IMODE(tool.paths.mcp_receipt_file.stat().st_mode), 0o600)

    @staticmethod
    def _tree(root: Path) -> dict[str, tuple[object, ...]]:
        entries: dict[str, tuple[object, ...]] = {}
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(root))
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                entries[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                entries[relative] = ("dir", mode)
            else:
                entries[relative] = ("file", mode, path.read_bytes())
        return entries

    def _failed_joplin_upgrade_case(self, active: set[str], *, restore_fails: bool = False) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    "3.7.1",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            tool.paths.mcp_binary.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.mcp_binary.write_bytes(b"previous adapter")
            npm_prefix = tool.paths.npm_prefix
            package = npm_prefix / "lib" / "node_modules" / "joplin"
            package.mkdir(parents=True)
            (package / "main.js").write_bytes(b"previous Joplin \x00\xff")
            (package / "main.js").chmod(0o755)
            (npm_prefix / "bin").mkdir()
            (npm_prefix / "bin" / "joplin").symlink_to("../lib/node_modules/joplin/main.js")
            (npm_prefix / "fixture-version").write_text("3.6.2")
            original_tree = self._tree(npm_prefix)
            original_units = {
                path: path.read_bytes()
                for path in (tool.paths.unit_path, tool.paths.adapter_unit_path)
            }
            # The real installer path runs this npm; install leaves a partial tree.
            npm = root / "npm"
            npm.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\nfrom pathlib import Path\n"
                "args = sys.argv[1:]\nprefix = Path(args[args.index('--prefix') + 1])\n"
                "marker = prefix / 'fixture-version'\n"
                "if args[0] == 'list':\n"
                "    print(json.dumps({'dependencies': {'joplin': {'version': marker.read_text()}}}"
                " if marker.exists() else {}))\n"
                "elif args[0] == 'install':\n"
                "    partial = prefix / 'lib' / 'node_modules' / 'joplin'\n"
                "    partial.mkdir(parents=True)\n"
                "    (partial / 'partial.js').write_text('partial')\n"
                "    sys.exit('npm ERR! fixture registry failure')\n"
                "else:\n    sys.exit(95)\n"
            )
            npm.chmod(0o700)
            dependencies = models.Dependencies(
                Path("/node"), npm, Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(
                    application,
                    "service_active",
                    side_effect=lambda _runner, _systemctl, unit: unit in active,
                ),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "installed_mcp_version", return_value="1.2.0"),
                mock.patch.object(
                    application, "download_mcp_binary", side_effect=AssertionError("no download")
                ),
                mock.patch.object(
                    application, "install_or_update_mcp", side_effect=AssertionError("unchanged")
                ),
                mock.patch.object(
                    application,
                    "remove_managed_path",
                    side_effect=ToolError("fixture removal failure") if restore_fails else None,
                    wraps=None if restore_fails else managed.remove_managed_path,
                ),
                mock.patch.object(application, "systemctl_command") as systemctl,
                mock.patch.object(application, "stop_after_startup_failure") as stop_failed,
                self.assertRaisesRegex(
                    ToolError,
                    "command failed"
                    if restore_fails
                    else r"Joplin 3\.7\.1 installation failed .*previous Joplin tree",
                ) as raised,
            ):
                tool.run()
            self.assertIn("fixture registry failure", str(raised.exception))
            if restore_fails:
                # Without a restored tree, services stay stopped and the backup is kept.
                backup = next(tool.paths.state_dir.glob("upgrade-backup-*"))
                self.assertEqual(self._tree(backup / "npm"), original_tree)
                self.assertEqual(
                    [call.args[2] for call in stop_failed.call_args_list],
                    ["joplin-md-sync.service", "joplin-terminal.service"],
                )
                self.assertNotIn("start", [call.args[2] for call in systemctl.call_args_list])
                return
            self.assertEqual(self._tree(npm_prefix), original_tree)
            self.assertEqual({path: path.read_bytes() for path in original_units}, original_units)
            self.assertEqual(tool.paths.mcp_binary.read_bytes(), b"previous adapter")
            backups = list(tool.paths.state_dir.glob("upgrade-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertFalse((backups[0] / "npm").exists())
            stop_failed.assert_not_called()
            adapter, terminal = "joplin-md-sync.service", "joplin-terminal.service"
            self.assertEqual(
                [call.args[2:] for call in systemctl.call_args_list],
                [
                    *([("stop", adapter)] if adapter in active else []),
                    *([("stop", terminal)] if terminal in active else []),
                    *([("start", terminal)] if terminal in active else []),
                    *([("start", adapter)] if adapter in active else []),
                ],
            )

    def test_unexpected_upgrade_failure_stops_both_services(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    "3.6.2",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=True),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(application, "installed_mcp_version", return_value="1.2.0"),
                mock.patch.object(application, "install_or_update_mcp", return_value="1.2.0"),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(application, "wait_for_api"),
                mock.patch.object(application, "wait_for_mcp"),
                mock.patch.object(
                    application, "smoke_test_mcp_service", side_effect=RuntimeError("defect")
                ),
                mock.patch.object(application, "stop_after_startup_failure") as stop_failed,
                self.assertRaisesRegex(RuntimeError, "defect"),
            ):
                tool.run()
            self.assertEqual(
                [call.args[2] for call in stop_failed.call_args_list],
                ["joplin-md-sync.service", "joplin-terminal.service"],
            )

    def test_failed_upgrade_healthcheck_stops_restart_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    "3.6.2",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=True),
                mock.patch.object(application, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(application, "installed_mcp_version", return_value="1.2.0"),
                mock.patch.object(application, "install_or_update_mcp", return_value="1.2.0"),
                mock.patch.object(credentials, "load_mcp_auth_token", return_value=None),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(
                    application,
                    "wait_for_api",
                    side_effect=ToolError("healthcheck failed"),
                ),
                mock.patch.object(application, "stop_after_startup_failure") as stop_failed,
                self.assertRaisesRegex(ToolError, "healthcheck failed"),
            ):
                tool.run()
            stop_failed.assert_any_call(
                tool.runner,
                dependencies.systemctl,
                "joplin-terminal.service",
            )

    def test_service_tokens_are_resolved_before_sync_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--sync-target",
                    "nextcloud",
                    "--sync-location",
                    "https://cloud.invalid/Joplin",
                    "--sync-username",
                    "user",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env, input_fn=lambda _prompt: "")
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            order: list[str] = []

            def sync_secret(*_args: object, **_kwargs: object) -> str:
                order.append("sync-password")
                raise ToolError("stop after prompt ordering check")

            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(
                    application,
                    "configure_lingering",
                    side_effect=lambda *_args, **_kwargs: order.append("lingering"),
                ),
                mock.patch.object(
                    credentials,
                    "resolve_gpt_actions_token",
                    side_effect=lambda *_args, **_kwargs: (
                        order.append("gpt-token") or GPT_ACTIONS_TOKEN
                    ),
                ),
                mock.patch.object(
                    credentials,
                    "resolve_mcp_auth_token",
                    side_effect=lambda *_args, **_kwargs: order.append("mcp-token") or "t" * 32,
                ),
                mock.patch.object(application, "service_active", return_value=False),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(
                    application,
                    "install_or_update_joplin",
                    return_value="3.6.2",
                ),
                mock.patch.object(sync, "resolve_secret", side_effect=sync_secret),
                mock.patch.object(application, "read_profile_settings", return_value={}),
                self.assertRaisesRegex(ToolError, "prompt ordering"),
            ):
                tool.run()
            self.assertEqual(
                order,
                ["mcp-token", "gpt-token", "lingering", "sync-password"],
            )

    def test_upgrade_restarts_both_services_and_smokes_both_api_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-version",
                    "3.6.2",
                    "--joplin-md-sync-version",
                    "1.2.0",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(application, "service_active", return_value=True),
                mock.patch.object(
                    application,
                    "resolve_service_tokens",
                    return_value=(MCP_TOKEN, GPT_ACTIONS_TOKEN),
                ),
                mock.patch.object(application, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(application, "installed_mcp_version", return_value="1.2.0"),
                mock.patch.object(application, "install_or_update_mcp", return_value="1.2.0"),
                mock.patch.object(application, "systemctl_command") as systemctl,
                mock.patch.object(application, "wait_for_api") as api_health,
                mock.patch.object(application, "wait_for_mcp") as mcp_health,
                mock.patch.object(application, "smoke_test_mcp_service") as mcp_smoke,
                mock.patch.object(
                    application,
                    "smoke_test_gpt_actions_service",
                ) as actions_smoke,
            ):
                tool.run()
            self.assertEqual(
                [call.args[2:] for call in systemctl.call_args_list],
                [
                    ("stop", "joplin-md-sync.service"),
                    ("stop", "joplin-terminal.service"),
                    ("daemon-reload",),
                    ("restart", "joplin-terminal.service"),
                    ("restart", "joplin-md-sync.service"),
                ],
            )
            api_health.assert_called_once_with(41185)
            mcp_health.assert_called_once_with(8765, MCP_TOKEN)
            mcp_smoke.assert_called_once_with(8765, MCP_TOKEN)
            actions_smoke.assert_called_once_with(8765, GPT_ACTIONS_TOKEN)

    def test_upgrade_resolves_both_latest_versions_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            joplin_version = "3.9.0"
            mcp_version = "2.0.0"
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
            }
            args = cli.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                    "--no-start-service",
                ]
            )
            tool = application.Installer(args, env=env)
            write_installed_units(tool)
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(application, "check_dependencies", return_value=dependencies),
                mock.patch.object(
                    application, "resolve_latest_version", return_value=joplin_version
                ),
                mock.patch.object(
                    application, "resolve_latest_mcp_version", return_value=mcp_version
                ),
                mock.patch.object(credentials, "load_mcp_auth_token", return_value=None),
                mock.patch.object(application, "service_active", return_value=False),
                mock.patch.object(application, "systemctl_command"),
                mock.patch.object(
                    application, "install_or_update_joplin", return_value=joplin_version
                ) as update_joplin,
                mock.patch.object(application, "installed_mcp_version", return_value=mcp_version),
                mock.patch.object(
                    application, "install_or_update_mcp", return_value=mcp_version
                ) as update_mcp,
            ):
                tool.run()
            update_joplin.assert_called_once_with(
                tool.runner,
                dependencies,
                tool.paths,
                joplin_version,
            )
            update_mcp.assert_called_once_with(tool.runner, tool.paths, mcp_version)


class DependencyTests(unittest.TestCase):
    def test_node_alias_symlink_is_not_dereferenced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snap_dispatcher = root / "snap"
            snap_dispatcher.write_text("dispatcher\n")
            node_alias = root / "node"
            node_alias.symlink_to(snap_dispatcher)
            programs = {
                "node": str(node_alias),
                "npm": "/usr/bin/npm",
                "systemctl": "/usr/bin/systemctl",
                "loginctl": "/usr/bin/loginctl",
            }
            runner = mock.Mock()
            runner.run.side_effect = [
                subprocess.CompletedProcess([], 0, "v22.0.0\n", ""),
                subprocess.CompletedProcess([], 0, "10.0.0\n", ""),
            ]
            with mock.patch.object(commands.shutil, "which", side_effect=programs.get):
                dependencies = commands.check_dependencies(runner)
            self.assertEqual(dependencies.node, node_alias.absolute())
            self.assertTrue(dependencies.node.is_symlink())
            self.assertEqual(runner.run.call_args_list[0].args[0][0], node_alias.absolute())

    def test_older_supported_python_warns_but_is_not_rejected(self) -> None:
        recommended = commands.RECOMMENDED_PYTHON
        older = (recommended[0], recommended[1] - 1, 5)
        node_version = "22.0.0"
        runner = mock.Mock()
        runner.run.side_effect = [
            subprocess.CompletedProcess([], 0, f"v{node_version}\n", ""),
            subprocess.CompletedProcess([], 0, "10.0.0\n", ""),
        ]
        programs = {
            "node": "/usr/bin/node",
            "npm": "/usr/bin/npm",
            "systemctl": "/usr/bin/systemctl",
            "loginctl": "/usr/bin/loginctl",
        }
        with (
            mock.patch.object(commands.sys, "version_info", older),
            mock.patch.object(
                commands.sys,
                "version",
                ".".join(str(part) for part in older) + " (test)",
            ),
            mock.patch.object(commands.shutil, "which", side_effect=programs.get),
            self.assertLogs(commands.LOG, level="WARNING") as logs,
        ):
            dependencies = commands.check_dependencies(runner)
        self.assertEqual(dependencies.node_version, node_version)
        self.assertIn(
            f"Python {recommended[0]}.{recommended[1]} is recommended",
            "\n".join(logs.output),
        )


class NpmTests(unittest.TestCase):
    def test_parse_installed_version(self) -> None:
        version = "3.6.2"
        self.assertEqual(
            releases.parse_npm_version(
                json.dumps({"dependencies": {"joplin": {"version": version}}})
            ),
            version,
        )
        self.assertIsNone(releases.parse_npm_version("{}"))
        self.assertIsNone(releases.parse_npm_version("not-json"))

    def test_unknown_launcher_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"HOME": str(root)}
            args = cli.build_parser(env).parse_args(["--joplin-prefix", str(root / ".local")])
            paths = cli.build_paths(args, env)
            paths.launcher.parent.mkdir(parents=True)
            paths.launcher.symlink_to("/unknown/joplin")
            with self.assertRaisesRegex(ToolError, "unknown Joplin launcher"):
                releases._safe_launcher_state(paths)

    def test_install_uses_isolated_global_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            version = "3.6.2"
            root = Path(temporary)
            env = {"HOME": str(root)}
            args = cli.build_parser(env).parse_args(["--joplin-prefix", str(root / ".local")])
            paths = cli.build_paths(args, env)
            dependencies = models.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            runner = mock.Mock()
            runner.run.return_value = subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.object(releases, "resolve_latest_version", return_value=version),
                mock.patch.object(releases, "_safe_launcher_state", return_value="missing"),
                mock.patch.object(
                    releases,
                    "installed_joplin_version",
                    side_effect=[None, None, version],
                ),
                mock.patch.object(releases, "smoke_test_joplin"),
                mock.patch.object(releases, "install_launcher"),
            ):
                actual = releases.install_or_update_joplin(runner, dependencies, paths, "latest")
            self.assertEqual(actual, version)
            runner.run.assert_called_once_with(
                [
                    dependencies.npm,
                    "install",
                    "--global",
                    "--prefix",
                    paths.npm_prefix,
                    f"joplin@{version}",
                ],
                timeout=releases.NPM_TIMEOUT,
            )


class McpReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version = "9.8.7"
        self.asset = "joplin-md-sync-linux-amd64"
        self.executable = self._fake_executable(self.version)
        release = self.root / "releases" / "download" / f"v{self.version}"
        release.mkdir(parents=True)
        (release / self.asset).write_bytes(self.executable)
        checksum = hashlib.sha256(self.executable).hexdigest()
        (release / "SHA256SUMS.txt").write_text(f"{checksum}  {self.asset}\n")

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                pass

        handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
            *args, directory=str(self.root), **kwargs
        )
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        env = {
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
        }
        args = cli.build_parser(env).parse_args(["--joplin-prefix", str(self.root / "prefix")])
        self.paths = cli.build_paths(args, env)
        self.runner = commands.CommandRunner(SecretRedactor(), os.environ)

    @staticmethod
    def _fake_executable(version: str) -> bytes:
        source = f"""#!{sys.executable}
import json
import sys

if sys.argv[1:] == ["version", "--json"]:
    print(json.dumps({{
        "distribution": "standalone",
        "repository": "https://github.com/kogeler/joplin-md-sync",
        "tool_version": {version!r},
    }}))
elif sys.argv[1:] == ["capabilities", "--json"]:
    print(json.dumps({{"commands": ["mcp serve"]}}))
else:
    raise SystemExit(2)
"""
        return source.encode()

    def test_architecture_asset_mapping(self) -> None:
        self.assertEqual(
            releases.mcp_asset_name("linux", "x86_64"),
            "joplin-md-sync-linux-amd64",
        )
        self.assertEqual(
            releases.mcp_asset_name("linux", "aarch64"),
            "joplin-md-sync-linux-arm64",
        )
        with self.assertRaisesRegex(ToolError, "no joplin-md-sync standalone"):
            releases.mcp_asset_name("linux", "riscv64")

    def test_download_verifies_checksum_and_is_idempotent(self) -> None:
        base = f"http://127.0.0.1:{self.server.server_address[1]}/releases"
        with (
            mock.patch.object(releases, "MCP_RELEASES_URL", base),
            mock.patch.object(releases, "mcp_asset_name", return_value=self.asset),
            mock.patch.object(releases, "resolve_latest_mcp_version", return_value=self.version),
        ):
            actual = releases.install_or_update_mcp(
                self.runner,
                self.paths,
                "latest",
            )
        self.assertEqual(actual, self.version)
        self.assertEqual(self.paths.mcp_binary.read_bytes(), self.executable)
        self.assertEqual(stat.S_IMODE(self.paths.mcp_binary.stat().st_mode), 0o700)
        with mock.patch.object(
            releases,
            "download_mcp_binary",
            side_effect=AssertionError("must not download an installed version"),
        ):
            self.assertEqual(
                releases.install_or_update_mcp(
                    self.runner,
                    self.paths,
                    self.version,
                ),
                self.version,
            )

    def test_checksum_mismatch_does_not_install_binary(self) -> None:
        checksum_file = self.root / "releases" / "download" / f"v{self.version}" / "SHA256SUMS.txt"
        checksum_file.write_text(f"{'0' * 64}  {self.asset}\n")
        base = f"http://127.0.0.1:{self.server.server_address[1]}/releases"
        with (
            mock.patch.object(releases, "MCP_RELEASES_URL", base),
            mock.patch.object(releases, "mcp_asset_name", return_value=self.asset),
            self.assertRaisesRegex(ToolError, "SHA-256 mismatch"),
        ):
            releases.install_or_update_mcp(self.runner, self.paths, self.version)
        self.assertFalse(self.paths.mcp_binary.exists())

    def test_latest_release_tag_is_validated(self) -> None:
        version = "2.3.4"
        with mock.patch.object(
            releases,
            "fetch_release_bytes",
            return_value=json.dumps({"tag_name": f"v{version}"}).encode(),
        ):
            self.assertEqual(releases.resolve_latest_mcp_version(), version)
        with (
            mock.patch.object(
                releases,
                "fetch_release_bytes",
                return_value=b'{"tag_name":"rolling"}',
            ),
            self.assertRaisesRegex(ToolError, "valid vX.Y.Z"),
        ):
            releases.resolve_latest_mcp_version()

    def test_unknown_existing_binary_is_not_overwritten(self) -> None:
        self.paths.mcp_binary.parent.mkdir(parents=True)
        self.paths.mcp_binary.write_bytes(
            self._fake_executable("9.8.7").replace(b'"standalone"', b'"wheel"')
        )
        self.paths.mcp_binary.chmod(0o700)
        with self.assertRaisesRegex(ToolError, "non-standalone"):
            releases.install_or_update_mcp(self.runner, self.paths, self.version)


class McpHealthcheckTests(unittest.TestCase):
    def test_smoke_rejects_joplin_tool_error(self) -> None:
        with (
            mock.patch.object(
                service,
                "mcp_post",
                side_effect=[
                    (200, {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}}),
                    (202, None),
                    (
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "result": {"isError": True},
                        },
                    ),
                ],
            ),
            self.assertRaisesRegex(ToolError, "reported an error"),
        ):
            service.smoke_test_mcp_service(8765, None)

    def test_smoke_lists_joplin_objects_with_optional_bearer_token(self) -> None:
        def handler_for(
            authorizations: list[str | None],
            testcase: unittest.TestCase,
        ) -> type[http.server.BaseHTTPRequestHandler]:
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    authorizations.append(self.headers.get("Authorization"))
                    length = int(self.headers["Content-Length"])
                    request = json.loads(self.rfile.read(length))
                    method = request["method"]
                    if method == "notifications/initialized":
                        self.send_response(202)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if method == "initialize":
                        result = {"serverInfo": {"name": "test", "version": "1"}}
                    else:
                        testcase.assertEqual(method, "tools/call")
                        testcase.assertEqual(
                            request["params"],
                            {
                                "name": "joplin_list_notebooks",
                                "arguments": {"limit": 1},
                            },
                        )
                        result = {
                            "isError": False,
                            "structuredContent": {
                                "notebooks": [{"id": "notebook-id"}],
                                "count": 1,
                            },
                        }
                    body = json.dumps(
                        {"jsonrpc": "2.0", "id": request["id"], "result": result}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, _format: str, *args: object) -> None:
                    pass

            return Handler

        for token in ("mcp-health-token-value-with-32-chars", None):
            authorizations: list[str | None] = []
            handler = handler_for(authorizations, self)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                service.smoke_test_mcp_service(int(server.server_address[1]), token, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            expected = f"Bearer {token}" if token else None
            self.assertEqual(authorizations, [expected, expected, expected])

    def test_authenticated_method_not_allowed_means_ready(self) -> None:
        token = "mcp-health-token-value-with-32-chars"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = int(server.server_address[1])
        self.assertTrue(ping_mcp(port, token, 1).ok)
        rejected = ping_mcp(port, "x" * 32, 1)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.reason, "authentication rejected")

    def test_unauthenticated_endpoint_is_ready_without_authorization_header(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.headers.get("Authorization") is not None:
                    self.send_response(400)
                else:
                    self.send_response(405)
                    self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = int(server.server_address[1])
        self.assertTrue(ping_mcp(port, None, 1).ok)


class GptActionsHealthcheckTests(unittest.TestCase):
    def test_smoke_requires_authentication_and_accepts_dedicated_token(self) -> None:
        token = "gpt-actions-health-token-value-12345"
        seen: list[str | None] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                authorization = self.headers.get("Authorization")
                seen.append(authorization)
                self.rfile.read(int(self.headers["Content-Length"]))
                status = 404 if authorization == f"Bearer {token}" else 401
                body = b'{"success":false}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            service.smoke_test_gpt_actions_service(
                int(server.server_address[1]),
                token,
                timeout=1,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(seen, [None, f"Bearer {token}"])

    def test_smoke_rejects_missing_actions_transport(self) -> None:
        with (
            mock.patch.object(
                service,
                "gpt_actions_probe_status",
                side_effect=[401, 405],
            ),
            self.assertRaisesRegex(ToolError, "route-isolation"),
        ):
            service.smoke_test_gpt_actions_service(8765, GPT_ACTIONS_TOKEN)


class SingleFileDownloadTests(unittest.TestCase):
    def test_downloaded_installer_loads_companion_assets(self) -> None:
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                pass

        handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
            *args, directory=str(TOOLS_DIR), **kwargs
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standalone = root / "install_joplin_terminal.py"
            shutil.copy2(TOOLS_DIR / "install_joplin_terminal.py", standalone)
            env = dict(os.environ)
            env["JOPLIN_TERMINAL_ASSET_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
            help_result = subprocess.run(
                [sys.executable, standalone, "--help"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("--upgrade", help_result.stdout)
            self.assertNotIn("--update-joplin", help_result.stdout)
            self.assertNotIn("--update-mcp", help_result.stdout)
            self.assertIn("--allow-remote-mcp", help_result.stdout)
            self.assertNotIn("--mcp-auth-token", help_result.stdout)
            self.assertIn("--enable-linger", help_result.stdout)
            asset_result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import importlib.util; "
                        "s=importlib.util.spec_from_file_location('downloaded_installer', "
                        "'install_joplin_terminal.py'); "
                        "m=importlib.util.module_from_spec(s); "
                        "import sys; sys.modules[s.name]=m; s.loader.exec_module(m); "
                        "ctx=m.installer_application(); ctx.__enter__(); from joplin_terminal_installer.assets import read_project_asset; print(len(read_project_asset('systemd/joplin-terminal.service'))); ctx.__exit__(None,None,None)"
                    ),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(asset_result.returncode, 0, asset_result.stderr)
            self.assertGreater(int(asset_result.stdout.strip()), 100)
            mcp_asset_result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import importlib.util; "
                        "s=importlib.util.spec_from_file_location('downloaded_installer', "
                        "'install_joplin_terminal.py'); "
                        "m=importlib.util.module_from_spec(s); "
                        "import sys; sys.modules[s.name]=m; s.loader.exec_module(m); "
                        "ctx=m.installer_application(); ctx.__enter__(); from joplin_terminal_installer.assets import read_project_asset; print(len(read_project_asset("
                        "'systemd/joplin-md-sync.service'))); ctx.__exit__(None,None,None)"
                    ),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(mcp_asset_result.returncode, 0, mcp_asset_result.stderr)
            self.assertGreater(int(mcp_asset_result.stdout.strip()), 100)

    def test_installer_runs_from_downloaded_stdin(self) -> None:
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                pass

        handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
            *args, directory=str(TOOLS_DIR), **kwargs
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(
            f"{base_url}/install_joplin_terminal.py",
            timeout=5,
        ) as response:
            source = response.read()
        env = dict(os.environ)
        env["JOPLIN_TERMINAL_ASSET_BASE_URL"] = base_url
        result = subprocess.run(
            [sys.executable, "-", "--help"],
            input=source,
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"--allow-remote-mcp", result.stdout)
        self.assertIn(b"--purge", result.stdout)

    def test_piped_program_reads_interactive_answer_from_controlling_tty(self) -> None:
        read_fd, write_fd = os.pipe()
        pid, tty_fd = pty.fork()
        if pid == 0:
            os.close(write_fd)
            os.dup2(read_fd, 0)
            os.close(read_fd)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(TOOLS_DIR)
            os.execve(sys.executable, [sys.executable, "-"], env)

        os.close(read_fd)
        source = (
            b"from joplin_terminal_installer.cli import terminal_input\n"
            b"answer = terminal_input('Linger prompt? ')\n"
            b"print(f'ANSWER={answer}', flush=True)\n"
        )
        os.write(write_fd, source)
        os.close(write_fd)
        output = bytearray()
        answered = False
        status: int | None = None
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                ready, _writable, _exceptional = select.select([tty_fd], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(tty_fd, 65536)
                    except OSError:
                        chunk = b""
                    output.extend(chunk)
                    if b"Linger prompt? " in output and not answered:
                        os.write(tty_fd, b"yes\n")
                        answered = True
                waited, child_status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    status = child_status
                    break
            if status is None:
                os.kill(pid, 9)
                _waited, status = os.waitpid(pid, 0)
                self.fail(f"piped TTY prompt timed out: {output!r}")
        finally:
            os.close(tty_fd)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors="replace"))
        self.assertIn(b"ANSWER=yes", output)


class DebugCollectorTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash unavailable")
    def test_collector_has_valid_bash_syntax_and_is_executable(self) -> None:
        collector = TOOLS_DIR / "collect_joplin_debug.sh"
        self.assertTrue(os.access(collector, os.X_OK))
        result = subprocess.run(
            ["bash", "-n", collector],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collector_does_not_read_secret_files_or_full_log_lines(self) -> None:
        source = (TOOLS_DIR / "collect_joplin_debug.sh").read_text()
        self.assertNotRegex(source, r"\bcat\s+[^\n]*(api-token|mcp-token)")
        self.assertNotIn("tail -n 150", source)
        self.assertIn("Joplin error keyword counts", source)
        self.assertIn('if [[ "$NODE_PATH" == /snap/bin/* ]]', source)
        self.assertIn("-p ProtectSystem=false", source)


if __name__ == "__main__":
    unittest.main()
