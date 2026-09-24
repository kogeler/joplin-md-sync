import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import config as config_mod
from joplin_md_sync.config import resolve_base_url, resolve_token
from joplin_md_sync.errors import ApiError, AuthError, UnsafeOperationError


class ResolveBaseUrlTest(unittest.TestCase):
    def test_builtin_default_used_when_it_answers(self):
        """Nothing configured + default endpoint alive -> no discovery scan."""
        with (
            mock.patch.object(config_mod, "ping_url", return_value=True) as ping,
            mock.patch.object(config_mod, "discover_base_url") as disc,
        ):
            url = resolve_base_url(env={})
        self.assertEqual(url, "http://127.0.0.1:41184")
        ping.assert_called_once_with("http://127.0.0.1:41184", timeout=2.0)
        disc.assert_not_called()

    def test_falls_back_to_discovery_when_default_silent(self):
        with (
            mock.patch.object(config_mod, "ping_url", return_value=False),
            mock.patch.object(
                config_mod, "discover_base_url", return_value="http://127.0.0.1:41187"
            ) as disc,
        ):
            url = resolve_base_url(env={})
        self.assertEqual(url, "http://127.0.0.1:41187")
        disc.assert_called_once_with(timeout=2.0)

    def test_custom_discovery_timeout_is_forwarded(self):
        with (
            mock.patch.object(config_mod, "ping_url", return_value=False) as ping,
            mock.patch.object(
                config_mod, "discover_base_url", return_value="http://127.0.0.1:41187"
            ) as disc,
        ):
            resolve_base_url(env={}, discovery_timeout=0.25)
        ping.assert_called_once_with("http://127.0.0.1:41184", timeout=0.25)
        disc.assert_called_once_with(timeout=0.25)

    def test_cli_beats_everything(self):
        with mock.patch.object(config_mod, "ping_url") as ping:
            url = resolve_base_url(
                cli_base_url="http://127.0.0.1:5555",
                cli_port=6666,
                workspace_base_url="http://127.0.0.1:7777",
                env={"JOPLIN_BASE_URL": "http://127.0.0.1:8888"},
            )
        self.assertEqual(url, "http://127.0.0.1:5555")
        ping.assert_not_called()

    def test_cli_port_beats_env(self):
        url = resolve_base_url(cli_port=6666, env={"JOPLIN_BASE_URL": "http://127.0.0.1:8888"})
        self.assertEqual(url, "http://127.0.0.1:6666")

    def test_env_base_url_beats_env_port_and_workspace(self):
        url = resolve_base_url(
            workspace_base_url="http://127.0.0.1:7777",
            env={"JOPLIN_BASE_URL": "http://localhost:8888", "JOPLIN_PORT": "9999"},
        )
        self.assertEqual(url, "http://localhost:8888")

    def test_env_port_used(self):
        url = resolve_base_url(env={"JOPLIN_PORT": "9999"})
        self.assertEqual(url, "http://127.0.0.1:9999")

    def test_invalid_env_port_rejected(self):
        with self.assertRaises(ApiError):
            resolve_base_url(env={"JOPLIN_PORT": "not-a-port"})

    def test_workspace_config_beats_default(self):
        with mock.patch.object(config_mod, "ping_url") as ping:
            url = resolve_base_url(workspace_base_url="http://127.0.0.1:7777", env={})
        self.assertEqual(url, "http://127.0.0.1:7777")
        ping.assert_not_called()

    def test_non_loopback_refused_without_flag(self):
        with self.assertRaises(UnsafeOperationError):
            resolve_base_url(cli_base_url="http://192.0.2.1:41184", env={})
        url = resolve_base_url(cli_base_url="http://192.0.2.1:41184", env={}, allow_remote=True)
        self.assertEqual(url, "http://192.0.2.1:41184")

    def test_invalid_scheme_rejected(self):
        with self.assertRaises(ApiError):
            resolve_base_url(cli_base_url="ftp://127.0.0.1:41184", env={})


class ResolveTokenTest(unittest.TestCase):
    def test_env_token(self):
        self.assertEqual(resolve_token(None, env={"JOPLIN_TOKEN": " tok "}), "tok")

    def test_missing_token(self):
        with self.assertRaises(AuthError):
            resolve_token(None, env={})

    def _protected_token_file(self, directory: str, content: str) -> Path:
        path = Path(directory) / "joplin-token"
        path.write_bytes(content.encode("ascii"))
        if os.name == "posix":
            path.chmod(0o600)
        return path

    def test_token_file_is_read_through_the_protected_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._protected_token_file(directory, " file-token \n")
            self.assertEqual(resolve_token(str(path), env={}), "file-token")
            self.assertEqual(resolve_token(str(path), env={"JOPLIN_TOKEN": "  "}), "file-token")
            if os.name == "posix":
                path.chmod(0o644)
                with self.assertRaisesRegex(AuthError, "group or others"):
                    resolve_token(str(path), env={})

    def test_token_file_and_env_together_are_a_conflict_before_reading(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(config_mod, "read_protected_token_line") as read,
        ):
            path = self._protected_token_file(directory, "file-token\n")
            with self.assertRaises(AuthError) as raised:
                resolve_token(str(path), env={"JOPLIN_TOKEN": "env-token"})
        read.assert_not_called()
        message = str(raised.exception)
        self.assertIn("(--token-file)", message)
        self.assertIn("JOPLIN_TOKEN", message)
        self.assertNotIn("file-token", message)
        self.assertNotIn("env-token", message)

    def test_blank_env_counts_as_unset(self):
        for blank in ("", "   "):
            with self.subTest(blank=blank), self.assertRaises(AuthError):
                resolve_token(None, env={"JOPLIN_TOKEN": blank})


if __name__ == "__main__":
    unittest.main()
