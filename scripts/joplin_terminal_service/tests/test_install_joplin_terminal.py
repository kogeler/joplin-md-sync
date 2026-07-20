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
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import install_joplin_terminal as installer  # noqa: E402
from joplin_terminal_common import (  # noqa: E402
    ProfileLock,
    SecretRedactor,
    ToolError,
    child_environment,
    ping_mcp,
    safe_command,
)


class ParserTests(unittest.TestCase):
    def test_cli_overrides_environment(self) -> None:
        env = {
            "HOME": "/tmp/home",
            "JOPLIN_NEXTCLOUD_URL": "https://env.invalid/Joplin",
            "JOPLIN_NEXTCLOUD_USER": "env-user",
            "JOPLIN_API_PORT": "41234",
        }
        args = installer.build_parser(env).parse_args(
            [
                "--nextcloud-url",
                "https://cli.invalid/Joplin",
                "--nextcloud-user",
                "cli-user",
                "--api-port",
                "42345",
            ]
        )
        self.assertEqual(args.nextcloud_url, "https://cli.invalid/Joplin")
        self.assertEqual(args.nextcloud_user, "cli-user")
        self.assertEqual(args.api_port, 42345)

    def test_defaults_resolve_latest_releases(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args([])
        self.assertEqual(args.api_port, 41185)
        self.assertEqual(args.mcp_port, 8765)
        self.assertEqual(args.joplin_version, "latest")
        self.assertEqual(args.joplin_md_sync_version, "latest")
        self.assertEqual(args.sync_interval, 300)
        self.assertFalse(args.upgrade)
        self.assertFalse(args.allow_remote_mcp)
        self.assertIsNone(args.mcp_auth_token)
        self.assertFalse(args.purge)
        self.assertFalse(args.yes)

    def test_upgrade_accepts_independent_version_overrides(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args(
            ["--upgrade", "--joplin-version", "3.7.1"]
        )
        installer.validate_args(args)
        self.assertTrue(args.upgrade)
        self.assertEqual(args.joplin_version, "3.7.1")
        self.assertEqual(args.joplin_md_sync_version, "latest")

    def test_unreleased_mcp_version_name_is_not_supported(self) -> None:
        with (
            mock.patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            installer.build_parser({"HOME": "/tmp/home"}).parse_args(
                ["--mcp-version", "1.2.0"]
            )

    def test_rejects_unsupported_sync_interval(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args(
            [
                "--nextcloud-url",
                "https://cloud.invalid/Joplin",
                "--nextcloud-user",
                "u",
                "--sync-interval",
                "123",
            ]
        )
        with self.assertRaisesRegex(ToolError, "must be one of"):
            installer.validate_args(args)

    def test_rejects_port_collision(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args(
            ["--upgrade", "--api-port", "41185", "--mcp-port", "41185"]
        )
        with self.assertRaisesRegex(ToolError, "must be different"):
            installer.validate_args(args)

    def test_remote_mcp_requires_nonempty_auth_token(self) -> None:
        with self.assertRaisesRegex(ToolError, "requires a non-empty"):
            installer.validate_mcp_auth_token(None, allow_remote=True)
        installer.validate_mcp_auth_token("t" * 32, allow_remote=True)

    def test_purge_does_not_require_nextcloud_and_rejects_upgrade(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args(["--purge"])
        installer.validate_args(args)
        combined = installer.build_parser({"HOME": "/tmp/home"}).parse_args(
            ["--purge", "--upgrade"]
        )
        with self.assertRaisesRegex(ToolError, "cannot be combined"):
            installer.validate_args(combined)

    def test_yes_is_only_valid_for_purge(self) -> None:
        args = installer.build_parser({"HOME": "/tmp/home"}).parse_args(["--yes"])
        with self.assertRaisesRegex(ToolError, "only valid with --purge"):
            installer.validate_args(args)


class SecretTests(unittest.TestCase):
    def test_child_environment_removes_every_service_secret(self) -> None:
        env = child_environment(
            {
                "PATH": "/usr/bin",
                "JOPLIN_TOKEN": "joplin-secret",
                "JOPLIN_GPT_ACTIONS_TOKEN": "actions-secret",
                "JOPLIN_MCP_AUTH_TOKEN": "mcp-secret",
                "JOPLIN_NEXTCLOUD_PASSWORD": "nextcloud-secret",
                "JOPLIN_E2EE_PASSWORD": "e2ee-secret",
            }
        )

        self.assertEqual(env["PATH"], "/usr/bin")
        for name in (
            "JOPLIN_TOKEN",
            "JOPLIN_GPT_ACTIONS_TOKEN",
            "JOPLIN_MCP_AUTH_TOKEN",
            "JOPLIN_NEXTCLOUD_PASSWORD",
            "JOPLIN_E2EE_PASSWORD",
        ):
            self.assertNotIn(name, env)

    def test_terminal_prompt_eof_is_a_tool_error(self) -> None:
        with (
            mock.patch("builtins.open", side_effect=OSError),
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaisesRegex(ToolError, "interactive input is unavailable"),
        ):
            installer.terminal_input("Continue? ")

    def test_secret_precedence(self) -> None:
        prompt = mock.Mock(return_value="prompt-secret")
        self.assertEqual(
            installer.resolve_secret(
                "cli-secret",
                "SECRET",
                "Password: ",
                env={"SECRET": "env-secret"},
                non_interactive=False,
                getpass_fn=prompt,
            ),
            "cli-secret",
        )
        self.assertFalse(prompt.called)
        self.assertEqual(
            installer.resolve_secret(
                None,
                "SECRET",
                "Password: ",
                env={"SECRET": "env-secret"},
                non_interactive=False,
                getpass_fn=prompt,
            ),
            "env-secret",
        )
        self.assertFalse(prompt.called)
        self.assertEqual(
            installer.resolve_secret(
                None,
                "SECRET",
                "Password: ",
                env={},
                non_interactive=False,
                getpass_fn=prompt,
            ),
            "prompt-secret",
        )

    def test_non_interactive_never_prompts(self) -> None:
        prompt = mock.Mock()
        with self.assertRaisesRegex(ToolError, "required secret is missing"):
            installer.resolve_secret(
                None,
                "SECRET",
                "Password: ",
                env={},
                non_interactive=True,
                getpass_fn=prompt,
            )
        prompt.assert_not_called()

    def test_secret_prompt_eof_is_a_tool_error(self) -> None:
        with self.assertRaisesRegex(ToolError, "interactive input.*unavailable"):
            installer.resolve_secret(
                None,
                "SECRET",
                "Password: ",
                env={},
                non_interactive=False,
                getpass_fn=mock.Mock(side_effect=EOFError),
            )

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
        runner = installer.CommandRunner(redactor, {"PATH": os.environ["PATH"]})
        with self.assertRaises(ToolError) as raised:
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

    def test_long_sensitive_command_has_bounded_output_and_safe_heartbeat(self) -> None:
        private_text = "private notebook title"
        runner = installer.CommandRunner(SecretRedactor(), {"PATH": os.environ["PATH"]})
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

    def test_mcp_token_cli_environment_and_prompt_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            redactor = SecretRedactor()
            env = {"HOME": temporary, "JOPLIN_MCP_AUTH_TOKEN": "e" * 32}
            cli_args = installer.build_parser(env).parse_args(["--mcp-auth-token", "c" * 32])
            cli_paths = installer.build_paths(cli_args, env)
            prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
            self.assertEqual(
                installer.resolve_mcp_auth_token(
                    cli_args,
                    cli_paths,
                    redactor,
                    getpass_fn=prompt,
                ),
                "c" * 32,
            )
            env_args = installer.build_parser(env).parse_args([])
            self.assertEqual(
                installer.resolve_mcp_auth_token(
                    env_args,
                    installer.build_paths(env_args, env),
                    redactor,
                    getpass_fn=prompt,
                ),
                "e" * 32,
            )
            interactive_env = {"HOME": str(root / "interactive")}
            interactive_args = installer.build_parser(interactive_env).parse_args([])
            empty_prompt = mock.Mock(return_value="")
            self.assertIsNone(
                installer.resolve_mcp_auth_token(
                    interactive_args,
                    installer.build_paths(interactive_args, interactive_env),
                    redactor,
                    getpass_fn=empty_prompt,
                )
            )
            empty_prompt.assert_called_once()

    def test_mcp_token_prompt_eof_is_a_tool_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = installer.build_parser(env).parse_args([])
            paths = installer.build_paths(args, env)
            with self.assertRaisesRegex(ToolError, "MCP token input is unavailable"):
                installer.resolve_mcp_auth_token(
                    args,
                    paths,
                    SecretRedactor(),
                    getpass_fn=mock.Mock(side_effect=EOFError),
                )

    def test_noninteractive_mcp_token_is_optional_and_preserved_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = installer.build_parser(env).parse_args(["--non-interactive"])
            paths = installer.build_paths(args, env)
            redactor = SecretRedactor()
            self.assertIsNone(installer.resolve_mcp_auth_token(args, paths, redactor))
            configured = "configured-mcp-bearer-token-value"
            installer.configure_mcp_auth_token(paths, configured)
            self.assertEqual(
                installer.resolve_mcp_auth_token(args, paths, redactor),
                configured,
            )

    def test_gpt_actions_token_is_required_and_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"HOME": temporary}
            args = installer.build_parser(env).parse_args(["--non-interactive"])
            paths = installer.build_paths(args, env)
            redactor = SecretRedactor()
            with self.assertRaisesRegex(ToolError, "bearer token is required"):
                installer.resolve_gpt_actions_token(
                    paths,
                    redactor,
                    env,
                    non_interactive=True,
                )
            token = "g" * 32
            installer.configure_gpt_actions_token(paths, token)
            self.assertEqual(
                installer.resolve_gpt_actions_token(
                    paths,
                    redactor,
                    env,
                    non_interactive=True,
                ),
                token,
            )

    def test_gpt_actions_token_environment_and_prompt_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = installer.build_paths(
                installer.build_parser({"HOME": temporary}).parse_args([]),
                {"HOME": temporary},
            )
            prompt = mock.Mock(return_value="p" * 32)
            self.assertEqual(
                installer.resolve_gpt_actions_token(
                    paths,
                    SecretRedactor(),
                    {"HOME": temporary, "JOPLIN_GPT_ACTIONS_TOKEN": "e" * 32},
                    non_interactive=False,
                    getpass_fn=prompt,
                ),
                "e" * 32,
            )
            prompt.assert_not_called()
            self.assertEqual(
                installer.resolve_gpt_actions_token(
                    paths,
                    SecretRedactor(),
                    {"HOME": temporary},
                    non_interactive=False,
                    getpass_fn=prompt,
                ),
                "p" * 32,
            )
            prompt.assert_called_once()


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
            "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
        }
        self.args = installer.build_parser(self.env).parse_args(
            [
                "--purge",
                "--yes",
                "--non-interactive",
                "--joplin-prefix",
                str(self.home / ".local"),
            ]
        )
        self.paths = installer.build_paths(self.args, self.env)

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
        self.paths.launcher.symlink_to(self.paths.npm_prefix / "bin" / "joplin")
        self.paths.unit_path.write_text("unit\n")
        self.paths.adapter_unit_path.write_text("adapter unit\n")
        self.paths.unit_path.with_name(f"{installer.SERVICE_NAME}.bak-test").write_text("backup\n")
        self.paths.adapter_unit_path.with_name(f"{installer.ADAPTER_SERVICE_NAME}.bak-test").write_text(
            "backup\n"
        )
        unrelated_bin = self.paths.launcher.parent / "keep-me"
        unrelated_bin.write_text("keep\n")
        unrelated_unit = self.paths.unit_path.parent / "keep-me.service"
        unrelated_unit.write_text("keep\n")
        return unrelated_bin, unrelated_unit

    def test_full_purge_removes_only_managed_local_paths_and_is_idempotent(self) -> None:
        unrelated_bin, unrelated_unit = self.populate()
        runner = self.stopped_runner()
        installer.purge_installation(
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

        installer.purge_installation(
            self.stopped_runner(),
            Path("/systemctl"),
            self.paths,
            self.env,
        )

    def test_profile_lock_blocks_purge_before_data_removal(self) -> None:
        self.populate()
        lock = ProfileLock(self.paths.lock_file)
        lock.acquire()
        self.addCleanup(lock.release)
        with self.assertRaisesRegex(ToolError, "profile is already in use"):
            installer.purge_installation(
                self.stopped_runner(),
                Path("/systemctl"),
                self.paths,
                self.env,
            )
        self.assertTrue((self.paths.profile_dir / "database.sqlite").is_file())

    def test_noninteractive_purge_requires_yes(self) -> None:
        self.args.yes = False
        with self.assertRaisesRegex(ToolError, "requires --yes"):
            installer.confirm_purge(self.args, self.paths)

    def test_interactive_purge_requires_exact_confirmation(self) -> None:
        self.args.yes = False
        self.args.non_interactive = False
        with self.assertRaisesRegex(ToolError, "cancelled"):
            installer.confirm_purge(self.args, self.paths, input_fn=lambda _prompt: "yes")
        installer.confirm_purge(self.args, self.paths, input_fn=lambda _prompt: "PURGE")

    def test_purge_dry_run_does_not_require_systemctl_or_delete_data(self) -> None:
        self.populate()
        self.args.dry_run = True
        tool = installer.Installer(
            self.args,
            env=self.env,
            input_fn=mock.Mock(side_effect=AssertionError("must not prompt")),
        )
        with mock.patch.object(
            installer,
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
        self.args = installer.build_parser(env).parse_args(
            [
                "--nextcloud-url",
                "https://cloud.invalid/Joplin",
                "--nextcloud-user",
                "user",
                "--joplin-prefix",
                str(self.root / "prefix"),
            ]
        )
        self.paths = installer.build_paths(self.args, env)

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
        content = installer.render_unit(
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

        mcp_content = installer.render_adapter_unit(
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
        self.assertIn("Requires=joplin-terminal.service", mcp_content)
        self.assertNotIn("a" * 32, mcp_content)

        unauthenticated = installer.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
            auth_enabled=False,
        )
        self.assertIn('"--host" "127.0.0.1"', unauthenticated)
        self.assertNotIn("--auth-token-file", unauthenticated)
        self.assertNotIn("--allow-remote-mcp", unauthenticated)

        remote = installer.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
            allow_remote=True,
            auth_enabled=True,
        )
        self.assertIn('"--host" "0.0.0.0"', remote)
        self.assertIn('"--allow-remote-mcp"', remote)
        self.assertIn('"--auth-token-file"', remote)
        with self.assertRaisesRegex(ToolError, "without bearer authentication"):
            installer.render_adapter_unit(
                (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
                paths=self.paths,
                api_port=41185,
                mcp_port=8765,
                allow_remote=True,
                auth_enabled=False,
            )

    def test_snap_node_unit_avoids_mount_namespace_hardening(self) -> None:
        snap_dispatcher = self.root / "snap"
        snap_dispatcher.write_text("dispatcher\n")
        node_alias = self.root / "node"
        node_alias.symlink_to(snap_dispatcher)
        content = installer.render_unit(
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
        content = installer.render_unit(
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

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_rendered_unit_passes_systemd_analyze(self) -> None:
        supervisor = (self.root / "deployed" / "runner.py").resolve()
        supervisor.parent.mkdir(parents=True)
        supervisor.write_text("pass\n")
        supervisor.chmod(0o700)
        content = installer.render_unit(
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
        adapter_unit.write_text(
            installer.render_adapter_unit(
                (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
                paths=self.paths,
                api_port=41185,
                mcp_port=8765,
            )
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
        changed = installer.install_unit(
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
        with mock.patch.object(installer, "read_setting", return_value=token):
            installer.extract_api_token(mock.Mock(), self.paths, redactor)
        self.assertEqual(self.paths.token_file.read_text(), f"{token}\n")
        self.assertEqual(stat.S_IMODE(self.paths.token_file.stat().st_mode), 0o600)
        self.assertNotIn(token, redactor.redact(token))

    def test_mcp_auth_token_is_stored_protected_and_can_be_disabled(self) -> None:
        redactor = SecretRedactor()
        token = "mcp-user-selected-token-value-1234"
        installer.configure_mcp_auth_token(self.paths, token)
        self.assertEqual(
            installer.load_mcp_auth_token(self.paths, redactor),
            token,
        )
        self.assertEqual(
            stat.S_IMODE(self.paths.mcp_auth_token_file.stat().st_mode),
            0o600,
        )
        unit = installer.render_adapter_unit(
            (TOOLS_DIR / "systemd" / "joplin-md-sync.service").read_text(),
            paths=self.paths,
            api_port=41185,
            mcp_port=8765,
        )
        self.assertNotIn(token, unit)
        installer.configure_mcp_auth_token(self.paths, None)
        self.assertFalse(self.paths.mcp_auth_token_file.exists())

    def test_gpt_actions_token_is_stored_protected_and_must_be_distinct(self) -> None:
        token = "g" * 32
        installer.configure_gpt_actions_token(self.paths, token)
        self.assertEqual(
            installer.load_gpt_actions_token(self.paths, SecretRedactor()),
            token,
        )
        self.assertEqual(
            stat.S_IMODE(self.paths.gpt_actions_token_file.stat().st_mode),
            0o600,
        )
        self.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.token_file.write_text("j" * 32)
        installer.validate_distinct_service_tokens(self.paths, token, "m" * 32)
        with self.assertRaisesRegex(ToolError, "MCP bearer tokens must be different"):
            installer.validate_distinct_service_tokens(self.paths, token, token)
        self.paths.token_file.write_text(token)
        with self.assertRaisesRegex(ToolError, "Joplin API tokens must be different"):
            installer.validate_distinct_service_tokens(self.paths, token, None)


class ConfigurationTests(unittest.TestCase):
    def test_default_target_with_choice_description_is_not_a_conflict(self) -> None:
        args = mock.Mock(
            nextcloud_url="https://new.invalid/Joplin",
            nextcloud_user="user",
            sync_interval=300,
            api_port=41185,
            force_reconfigure=False,
            non_interactive=True,
        )
        paths = mock.Mock(profile_dir=Path("/profile"))
        runner = mock.Mock()
        with (
            mock.patch.object(
                installer,
                "read_setting",
                side_effect=[
                    "0 (0: (None), 5: Nextcloud, 6: WebDAV)",
                    "null",
                ],
            ),
            mock.patch.object(installer, "write_setting") as write,
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "chmod"),
        ):
            installer.configure_profile(runner, paths, args, "nextcloud-password")
        self.assertEqual(write.call_args_list[0].args[-2:], ("sync.target", "5"))

    def test_force_reconfigure(self) -> None:
        args = mock.Mock(
            nextcloud_url="https://new.invalid/Joplin",
            force_reconfigure=True,
            non_interactive=True,
        )
        installer.confirm_reconfigure(args, "6", "https://old.invalid/Joplin")

    def test_noninteractive_conflict_requires_force(self) -> None:
        args = mock.Mock(
            nextcloud_url="https://new.invalid/Joplin",
            force_reconfigure=False,
            non_interactive=True,
        )
        with self.assertRaisesRegex(ToolError, "--force-reconfigure"):
            installer.confirm_reconfigure(args, "5", "https://old.invalid/Joplin")

    def test_interactive_conflict_can_be_rejected(self) -> None:
        args = mock.Mock(
            nextcloud_url="https://new.invalid/Joplin",
            force_reconfigure=False,
            non_interactive=False,
        )
        with self.assertRaisesRegex(ToolError, "cancelled"):
            installer.confirm_reconfigure(
                args,
                "5",
                "https://old.invalid/Joplin",
                input_fn=lambda _prompt: "n",
            )


class LingeringTests(unittest.TestCase):
    def test_enabled_lingering_does_not_prompt(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "yes\n", "")
        prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
        args = mock.Mock(enable_linger=False, non_interactive=False)
        with mock.patch.object(installer, "current_username", return_value="agent"):
            self.assertTrue(
                installer.configure_lingering(
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
        with mock.patch.object(installer, "current_username", return_value="agent"):
            self.assertTrue(
                installer.configure_lingering(
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
        with mock.patch.object(installer, "current_username", return_value="agent"):
            self.assertFalse(installer.configure_lingering(runner, Path("/loginctl"), args))
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
        with mock.patch.object(installer, "current_username", return_value="agent"):
            self.assertTrue(
                installer.configure_lingering(
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
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
                [
                    "--nextcloud-url",
                    "https://cloud.invalid/Joplin",
                    "--nextcloud-user",
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
            tool = installer.Installer(
                args,
                env=env,
                getpass_fn=prompt,
                redactor=redactor,
            )
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with mock.patch.object(installer, "check_dependencies", return_value=dependencies):
                tool.run()
            self.assertFalse((root / "prefix").exists())
            self.assertFalse((root / "profile").exists())
            prompt.assert_not_called()

    def test_upgrade_updates_both_without_resolving_sync_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
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
                    "--no-start-service",
                ]
            )
            prompt = mock.Mock(side_effect=AssertionError("must not prompt"))
            tool = installer.Installer(args, env=env, getpass_fn=prompt)
            tool.paths.unit_path.parent.mkdir(parents=True)
            tool.paths.unit_path.write_text("unit\n")
            tool.paths.adapter_unit_path.write_text("unit\n")
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(installer, "check_dependencies", return_value=dependencies),
                mock.patch.object(installer, "service_active", return_value=False),
                mock.patch.object(installer, "systemctl_command"),
                mock.patch.object(
                    installer, "install_or_update_joplin", return_value="3.6.2"
                ) as update_joplin,
                mock.patch.object(
                    installer, "install_or_update_mcp", return_value="1.2.0"
                ) as update_mcp,
                mock.patch.object(installer, "configure_profile") as configure,
                mock.patch.object(installer, "bootstrap_e2ee") as bootstrap,
            ):
                tool.run()
            update_joplin.assert_called_once_with(
                tool.runner,
                dependencies,
                tool.paths,
                "3.6.2",
            )
            update_mcp.assert_called_once_with(tool.runner, tool.paths, "1.2.0")
            configure.assert_not_called()
            bootstrap.assert_not_called()
            prompt.assert_not_called()
            self.assertFalse((root / "profile").exists())


class ServiceLifecycleTests(unittest.TestCase):
    def test_failed_upgrade_healthcheck_stops_restart_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
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
            tool = installer.Installer(args, env=env)
            tool.paths.unit_path.parent.mkdir(parents=True)
            tool.paths.unit_path.write_text("unit\n")
            tool.paths.adapter_unit_path.write_text("unit\n")
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(installer, "check_dependencies", return_value=dependencies),
                mock.patch.object(installer, "service_active", return_value=True),
                mock.patch.object(installer, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(installer, "install_or_update_mcp", return_value="1.2.0"),
                mock.patch.object(installer, "load_mcp_auth_token", return_value=None),
                mock.patch.object(installer, "systemctl_command"),
                mock.patch.object(
                    installer,
                    "wait_for_api",
                    side_effect=ToolError("healthcheck failed"),
                ),
                mock.patch.object(installer, "stop_after_startup_failure") as stop_failed,
                self.assertRaisesRegex(ToolError, "healthcheck failed"),
            ):
                tool.run()
            stop_failed.assert_called_once_with(
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
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
                [
                    "--nextcloud-url",
                    "https://cloud.invalid/Joplin",
                    "--nextcloud-user",
                    "user",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                ]
            )
            tool = installer.Installer(args, env=env)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            order: list[str] = []

            def sync_secret(*_args: object, **_kwargs: object) -> str:
                order.append("nextcloud-password")
                raise ToolError("stop after prompt ordering check")

            with (
                mock.patch.object(installer, "check_dependencies", return_value=dependencies),
                mock.patch.object(
                    installer,
                    "configure_lingering",
                    side_effect=lambda *_args, **_kwargs: order.append("lingering"),
                ),
                mock.patch.object(
                    installer,
                    "resolve_gpt_actions_token",
                    side_effect=lambda *_args, **_kwargs: order.append("gpt-token")
                    or "g" * 32,
                ),
                mock.patch.object(
                    installer,
                    "resolve_mcp_auth_token",
                    side_effect=lambda *_args, **_kwargs: order.append("mcp-token") or "t" * 32,
                ),
                mock.patch.object(installer, "service_active", return_value=False),
                mock.patch.object(installer, "systemctl_command"),
                mock.patch.object(
                    installer,
                    "install_or_update_joplin",
                    return_value="3.6.2",
                ),
                mock.patch.object(installer, "resolve_secret", side_effect=sync_secret),
                self.assertRaisesRegex(ToolError, "prompt ordering"),
            ):
                tool.run()
            self.assertEqual(
                order,
                ["lingering", "gpt-token", "mcp-token", "nextcloud-password"],
            )

    def test_upgrade_restarts_both_services_and_smokes_both_api_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
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
            tool = installer.Installer(args, env=env)
            tool.paths.unit_path.parent.mkdir(parents=True)
            tool.paths.unit_path.write_text("unit\n")
            tool.paths.adapter_unit_path.write_text("unit\n")
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(installer, "check_dependencies", return_value=dependencies),
                mock.patch.object(installer, "service_active", return_value=True),
                mock.patch.object(installer, "install_or_update_joplin", return_value="3.6.2"),
                mock.patch.object(installer, "install_or_update_mcp", return_value="1.2.0"),
                mock.patch.object(installer, "load_mcp_auth_token", return_value="t" * 32),
                mock.patch.object(installer, "systemctl_command") as systemctl,
                mock.patch.object(installer, "wait_for_api") as api_health,
                mock.patch.object(installer, "wait_for_mcp") as mcp_health,
                mock.patch.object(installer, "smoke_test_mcp_service") as mcp_smoke,
                mock.patch.object(
                    installer,
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
            mcp_health.assert_called_once_with(8765, "t" * 32)
            mcp_smoke.assert_called_once_with(8765, "t" * 32)
            actions_smoke.assert_called_once_with(8765, "g" * 32)

    def test_upgrade_resolves_both_latest_versions_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ["PATH"],
                "JOPLIN_GPT_ACTIONS_TOKEN": "g" * 32,
            }
            args = installer.build_parser(env).parse_args(
                [
                    "--upgrade",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "profile"),
                    "--no-start-service",
                ]
            )
            tool = installer.Installer(args, env=env)
            tool.paths.unit_path.parent.mkdir(parents=True)
            tool.paths.unit_path.write_text("unit\n")
            tool.paths.adapter_unit_path.write_text("unit\n")
            tool.paths.token_file.parent.mkdir(parents=True, exist_ok=True)
            tool.paths.token_file.write_text("j" * 32)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            with (
                mock.patch.object(installer, "check_dependencies", return_value=dependencies),
                mock.patch.object(installer, "resolve_latest_version", return_value="3.9.0"),
                mock.patch.object(installer, "resolve_latest_mcp_version", return_value="2.0.0"),
                mock.patch.object(installer, "load_mcp_auth_token", return_value=None),
                mock.patch.object(installer, "service_active", return_value=False),
                mock.patch.object(installer, "systemctl_command"),
                mock.patch.object(
                    installer, "install_or_update_joplin", return_value="3.9.0"
                ) as update_joplin,
                mock.patch.object(
                    installer, "install_or_update_mcp", return_value="2.0.0"
                ) as update_mcp,
            ):
                tool.run()
            update_joplin.assert_called_once_with(
                tool.runner,
                dependencies,
                tool.paths,
                "3.9.0",
            )
            update_mcp.assert_called_once_with(tool.runner, tool.paths, "2.0.0")


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
            with mock.patch.object(installer.shutil, "which", side_effect=programs.get):
                dependencies = installer.check_dependencies(runner)
            self.assertEqual(dependencies.node, node_alias.absolute())
            self.assertTrue(dependencies.node.is_symlink())
            self.assertEqual(runner.run.call_args_list[0].args[0][0], node_alias.absolute())

    def test_python_313_warns_but_is_not_rejected(self) -> None:
        runner = mock.Mock()
        runner.run.side_effect = [
            subprocess.CompletedProcess([], 0, "v22.0.0\n", ""),
            subprocess.CompletedProcess([], 0, "10.0.0\n", ""),
        ]
        programs = {
            "node": "/usr/bin/node",
            "npm": "/usr/bin/npm",
            "systemctl": "/usr/bin/systemctl",
            "loginctl": "/usr/bin/loginctl",
        }
        with (
            mock.patch.object(installer.sys, "version_info", (3, 13, 5)),
            mock.patch.object(installer.sys, "version", "3.13.5 (test)"),
            mock.patch.object(installer.shutil, "which", side_effect=programs.get),
            self.assertLogs(installer.LOG, level="WARNING") as logs,
        ):
            dependencies = installer.check_dependencies(runner)
        self.assertEqual(dependencies.node_version, "22.0.0")
        self.assertIn("Python 3.14 is recommended", "\n".join(logs.output))


class NpmTests(unittest.TestCase):
    def test_parse_installed_version(self) -> None:
        self.assertEqual(
            installer.parse_npm_version('{"dependencies":{"joplin":{"version":"3.6.2"}}}'),
            "3.6.2",
        )
        self.assertIsNone(installer.parse_npm_version("{}"))
        self.assertIsNone(installer.parse_npm_version("not-json"))

    def test_unknown_launcher_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"HOME": str(root)}
            args = installer.build_parser(env).parse_args(["--joplin-prefix", str(root / ".local")])
            paths = installer.build_paths(args, env)
            paths.launcher.parent.mkdir(parents=True)
            paths.launcher.symlink_to("/unknown/joplin")
            with self.assertRaisesRegex(ToolError, "unknown Joplin launcher"):
                installer._safe_launcher_state(paths)

    def test_install_uses_isolated_global_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"HOME": str(root)}
            args = installer.build_parser(env).parse_args(["--joplin-prefix", str(root / ".local")])
            paths = installer.build_paths(args, env)
            dependencies = installer.Dependencies(
                Path("/node"), Path("/npm"), Path("/systemctl"), "22.0.0", "10.0.0"
            )
            runner = mock.Mock()
            runner.run.return_value = subprocess.CompletedProcess([], 0, "", "")
            with (
                mock.patch.object(installer, "resolve_latest_version", return_value="3.6.2"),
                mock.patch.object(installer, "_safe_launcher_state", return_value="missing"),
                mock.patch.object(
                    installer,
                    "installed_joplin_version",
                    side_effect=[None, None, "3.6.2"],
                ),
                mock.patch.object(installer, "smoke_test_joplin"),
                mock.patch.object(installer, "install_launcher"),
            ):
                actual = installer.install_or_update_joplin(runner, dependencies, paths, "latest")
            self.assertEqual(actual, "3.6.2")
            runner.run.assert_called_once_with(
                [
                    dependencies.npm,
                    "install",
                    "--global",
                    "--prefix",
                    paths.npm_prefix,
                    "joplin@3.6.2",
                ],
                timeout=installer.NPM_TIMEOUT,
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
        args = installer.build_parser(env).parse_args(
            ["--joplin-prefix", str(self.root / "prefix")]
        )
        self.paths = installer.build_paths(args, env)
        self.runner = installer.CommandRunner(SecretRedactor(), os.environ)

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
            installer.mcp_asset_name("linux", "x86_64"),
            "joplin-md-sync-linux-amd64",
        )
        self.assertEqual(
            installer.mcp_asset_name("linux", "aarch64"),
            "joplin-md-sync-linux-arm64",
        )
        with self.assertRaisesRegex(ToolError, "no joplin-md-sync standalone"):
            installer.mcp_asset_name("linux", "riscv64")

    def test_download_verifies_checksum_and_is_idempotent(self) -> None:
        base = f"http://127.0.0.1:{self.server.server_address[1]}/releases"
        with (
            mock.patch.object(installer, "MCP_RELEASES_URL", base),
            mock.patch.object(installer, "mcp_asset_name", return_value=self.asset),
            mock.patch.object(
                installer, "resolve_latest_mcp_version", return_value=self.version
            ),
        ):
            actual = installer.install_or_update_mcp(
                self.runner,
                self.paths,
                "latest",
            )
        self.assertEqual(actual, self.version)
        self.assertEqual(self.paths.mcp_binary.read_bytes(), self.executable)
        self.assertEqual(stat.S_IMODE(self.paths.mcp_binary.stat().st_mode), 0o700)
        with mock.patch.object(
            installer,
            "download_mcp_binary",
            side_effect=AssertionError("must not download an installed version"),
        ):
            self.assertEqual(
                installer.install_or_update_mcp(
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
            mock.patch.object(installer, "MCP_RELEASES_URL", base),
            mock.patch.object(installer, "mcp_asset_name", return_value=self.asset),
            self.assertRaisesRegex(ToolError, "SHA-256 mismatch"),
        ):
            installer.install_or_update_mcp(self.runner, self.paths, self.version)
        self.assertFalse(self.paths.mcp_binary.exists())

    def test_latest_release_tag_is_validated(self) -> None:
        with mock.patch.object(
            installer,
            "fetch_release_bytes",
            return_value=b'{"tag_name":"v2.3.4"}',
        ):
            self.assertEqual(installer.resolve_latest_mcp_version(), "2.3.4")
        with (
            mock.patch.object(
                installer,
                "fetch_release_bytes",
                return_value=b'{"tag_name":"rolling"}',
            ),
            self.assertRaisesRegex(ToolError, "valid vX.Y.Z"),
        ):
            installer.resolve_latest_mcp_version()

    def test_unknown_existing_binary_is_not_overwritten(self) -> None:
        self.paths.mcp_binary.parent.mkdir(parents=True)
        self.paths.mcp_binary.write_bytes(
            self._fake_executable("9.8.7").replace(b'"standalone"', b'"wheel"')
        )
        self.paths.mcp_binary.chmod(0o700)
        with self.assertRaisesRegex(ToolError, "non-standalone"):
            installer.install_or_update_mcp(self.runner, self.paths, self.version)


class McpHealthcheckTests(unittest.TestCase):
    def test_smoke_rejects_joplin_tool_error(self) -> None:
        with (
            mock.patch.object(
                installer,
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
            installer.smoke_test_mcp_service(8765, None)

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
                installer.smoke_test_mcp_service(int(server.server_address[1]), token, 1)
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
            installer.smoke_test_gpt_actions_service(
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
                installer,
                "gpt_actions_probe_status",
                side_effect=[401, 405],
            ),
            self.assertRaisesRegex(ToolError, "route-isolation"),
        ):
            installer.smoke_test_gpt_actions_service(8765, "g" * 32)


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
            self.assertIn("--mcp-auth-token", help_result.stdout)
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
                        "print(len(m.read_project_asset('systemd/joplin-terminal.service')))"
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
                        "print(len(m.read_project_asset("
                        "'systemd/joplin-md-sync.service')))"
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
            b"from install_joplin_terminal import terminal_input\n"
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
