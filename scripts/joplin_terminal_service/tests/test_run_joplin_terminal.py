from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import install_joplin_terminal as installer  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class E2eePtyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fake = self.root / "fake_joplin.py"
        shutil.copy2(TOOLS_DIR / "tests" / "fake_joplin.py", self.fake)
        self.fake.chmod(0o700)
        self.profile = self.root / "profile"
        self.profile.mkdir()

    def run_fake(
        self, mode: str, password: str = "correct-password", timeout: float = 3.0
    ) -> installer.E2eeResult:
        env = dict(os.environ)
        env["FAKE_JOPLIN_MODE"] = mode
        return installer.run_e2ee_decrypt_pty(
            self.fake,
            self.profile,
            password,
            timeout=timeout,
            env=env,
        )

    def test_success_and_secret_not_echoed(self) -> None:
        result = self.run_fake("e2ee_success")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.prompts, 1)
        self.assertIn(b"Completed decryption.", result.output)
        self.assertNotIn(b"correct-password", result.output)

    def test_wrong_password_is_detected_even_with_exit_zero(self) -> None:
        result = self.run_fake("e2ee_wrong")
        self.assertEqual(result.exit_code, 0)
        self.assertIn(b"Invalid password", result.output)

    def test_changed_prompt_is_supported(self) -> None:
        result = self.run_fake("e2ee_changed_prompt")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.prompts, 1)
        self.assertIn(b"Completed decryption.", result.output)

    def test_no_master_key(self) -> None:
        result = self.run_fake("e2ee_no_key")
        self.assertEqual(result.exit_code, 1)
        self.assertIn(b"masterKeyNotLoaded", result.output)

    def test_timeout_terminates_child(self) -> None:
        result = self.run_fake("e2ee_timeout", timeout=0.2)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)

    def test_crash(self) -> None:
        result = self.run_fake("e2ee_crash")
        self.assertEqual(result.exit_code, 9)


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fake = self.root / "fake_joplin.py"
        shutil.copy2(TOOLS_DIR / "tests" / "fake_joplin.py", self.fake)
        self.fake.chmod(0o700)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.port = free_port()
        self.pid_file = self.root / "child.pid"
        self.processes: list[subprocess.Popen[str]] = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def start(self, mode: str, **overrides: str) -> subprocess.Popen[str]:
        env = dict(os.environ)
        env.update(
            {
                "FAKE_JOPLIN_MODE": mode,
                "FAKE_API_PORT": str(self.port),
                "FAKE_PID_FILE": str(self.pid_file),
            }
        )
        env.update(overrides)
        command = [
            sys.executable,
            str(TOOLS_DIR / "run_joplin_terminal.py"),
            "--node-path",
            sys.executable,
            "--joplin-path",
            str(self.fake),
            "--profile-dir",
            str(self.profile),
            "--lock-file",
            str(self.root / "profile.lock"),
            "--api-port",
            str(self.port),
            "--startup-timeout",
            "3",
            "--shutdown-timeout",
            "0.3",
            "--health-interval",
            "0.2",
            "--health-failures",
            "2",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.processes.append(process)
        return process

    def wait_ping(self, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/ping", timeout=0.2
                ) as response:
                    if response.read() == b"JoplinClipperServer":
                        return
            except OSError:
                pass
            time.sleep(0.05)
        self.fail("fake Joplin API did not become ready")

    def test_start_ping_and_sigterm(self) -> None:
        process = self.start("success")
        self.wait_ping()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("fake private note body", stdout + stderr)

    def test_api_start_failure_returns_nonzero(self) -> None:
        process = self.start("no_api")
        _stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1, stderr)

    def test_sigterm_during_startup_is_clean(self) -> None:
        process = self.start("no_api")
        deadline = time.monotonic() + 2
        while not self.pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.pid_file.exists())
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        child_pid = int(self.pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_child_crash_returns_nonzero(self) -> None:
        process = self.start("crash")
        _stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1, stderr)

    def test_health_loss_restarts_via_failure_exit(self) -> None:
        process = self.start("health_loss", FAKE_HEALTH_LOSS_DELAY="0.3")
        self.wait_ping()
        _stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1, stderr)

    def test_hung_child_is_killed_without_orphan(self) -> None:
        process = self.start("ignore_term")
        self.wait_ping()
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=4)
        self.assertEqual(process.returncode, 0, stderr)
        child_pid = int(self.pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_port_collision_is_rejected_before_spawn(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            process = self.start("success")
            _stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 1, stderr)
        self.assertIn("already in use", stderr)


if __name__ == "__main__":
    unittest.main()
