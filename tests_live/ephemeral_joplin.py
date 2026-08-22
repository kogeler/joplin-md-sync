"""Session-scoped isolated Joplin Desktop runtime for live protocol tests."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

JOPLIN_VERSION = "3.6.15"
JOPLIN_DEB_URL = (
    "https://github.com/laurent22/joplin/releases/download/"
    f"v{JOPLIN_VERSION}/Joplin-{JOPLIN_VERSION}.deb"
)
JOPLIN_DEB_SIZE = 104_324_556
JOPLIN_DEB_SHA256 = "c9fc77c077f1c81c581324dfdd4cc785307ea3c5b5f19ceecf9ee20fa78ac792"
_running: RunningJoplin | None = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class RunningJoplin:
    version: str
    base_url: str
    port: int
    token_file: Path


class EphemeralJoplin:
    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._joplin_process: subprocess.Popen[str] | None = None
        self._xvfb_process: subprocess.Popen[str] | None = None
        self._log_handles: list[TextIO] = []
        self.runtime: RunningJoplin | None = None

    @staticmethod
    def _run(
        command: list[str],
        *,
        environment: dict[str, str],
        label: str,
        timeout: float = 120,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"{label} failed: {type(exc).__name__}") from None
        if completed.returncode == 0:
            return completed
        detail_text = " ".join(f"{completed.stdout}\n{completed.stderr}".split())
        detail = f": {detail_text[-1000:]}" if detail_text else ""
        raise RuntimeError(f"{label} failed with exit {completed.returncode}{detail}")

    @staticmethod
    def _isolated_environment(root: Path) -> dict[str, str]:
        environment = dict(os.environ)
        for name in (
            "DISPLAY",
            "JOPLIN_BASE_URL",
            "JOPLIN_PORT",
            "JOPLIN_TOKEN",
            "NODE_OPTIONS",
            "NODE_PATH",
            "NPM_CONFIG_PREFIX",
            "WAYLAND_DISPLAY",
        ):
            environment.pop(name, None)
        home = root / "home"
        environment.update(
            {
                "HOME": str(home),
                "TMPDIR": str(root / "tmp"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
            }
        )
        return environment

    @staticmethod
    def _download_release(path: Path) -> None:
        request = urllib.request.Request(
            JOPLIN_DEB_URL,
            headers={"User-Agent": "joplin-md-sync-live-tests"},
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise RuntimeError(
                f"download Joplin Desktop {JOPLIN_VERSION} failed: {type(exc).__name__}"
            ) from None
        if size != JOPLIN_DEB_SIZE or digest.hexdigest() != JOPLIN_DEB_SHA256:
            raise RuntimeError(
                f"Joplin Desktop {JOPLIN_VERSION} release artifact failed integrity verification"
            )

    @staticmethod
    def _start_xvfb(
        xvfb: str,
        root: Path,
        environment: dict[str, str],
    ) -> tuple[subprocess.Popen[str], str, TextIO]:
        for number in range(90, 190):
            display = f":{number}"
            socket_path = Path(f"/tmp/.X11-unix/X{number}")
            lock_path = Path(f"/tmp/.X{number}-lock")
            if socket_path.exists() or lock_path.exists():
                continue
            log_handle = (root / "xvfb.log").open("w", encoding="utf-8")
            process = subprocess.Popen(
                [
                    xvfb,
                    display,
                    "-screen",
                    "0",
                    "1280x800x24",
                    "-nolisten",
                    "tcp",
                    "-noreset",
                ],
                cwd=root,
                env=environment,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if socket_path.exists():
                    return process, display, log_handle
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            EphemeralJoplin._stop_process_group(process)
            log_handle.close()
        raise RuntimeError("could not start an isolated Xvfb display")

    @staticmethod
    def _stop_process_group(process: subprocess.Popen[str] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=5)

    @staticmethod
    def _log_tail(path: Path) -> str:
        try:
            return " ".join(path.read_text(encoding="utf-8", errors="replace").split())[-1500:]
        except OSError:
            return ""

    @staticmethod
    def _wait_until_ready(
        process: subprocess.Popen[str],
        *,
        base_url: str,
        settings_file: Path,
        token_file: Path,
        log_file: Path,
        timeout: float = 90,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_error = "not attempted"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8"))
                token = settings.get("api.token")
                if not isinstance(token, str) or len(token) < 32:
                    raise ValueError("Joplin token is not ready")
                query = urllib.parse.urlencode({"token": token, "limit": 1, "fields": "id"})
                with urllib.request.urlopen(f"{base_url}/notes?{query}", timeout=1) as response:
                    payload = json.load(response)
                if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                    token_file.write_text(f"{token}\n", encoding="utf-8")
                    token_file.chmod(0o600)
                    return
                last_error = "unexpected notes response"
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(0.1)
        detail = EphemeralJoplin._log_tail(log_file)
        suffix = f"; {detail}" if detail else ""
        raise RuntimeError(f"ephemeral Joplin did not become ready ({last_error}){suffix}")

    def start(self) -> RunningJoplin:
        if self.runtime is not None:
            return self.runtime
        system = platform.system().lower()
        machine = platform.machine().lower()
        if system != "linux":
            raise RuntimeError(f"ephemeral Joplin live tests require Linux, found {system}")
        if machine not in {"amd64", "x86_64"}:
            raise RuntimeError(f"ephemeral Joplin live tests require Linux AMD64, found {machine}")
        dpkg_deb = shutil.which("dpkg-deb")
        xvfb = shutil.which("Xvfb")
        if dpkg_deb is None or xvfb is None:
            raise RuntimeError("ephemeral Joplin live tests require dpkg-deb and Xvfb")

        self._temporary = tempfile.TemporaryDirectory(prefix="jms-live-joplin-", dir="/tmp")
        root = Path(self._temporary.name)
        package = root / f"Joplin-{JOPLIN_VERSION}.deb"
        application_root = root / "application"
        profile = root / "profile"
        electron_data = root / "electron-data"
        token_file = root / "joplin-token"
        for directory in (
            root / "home",
            root / "runtime",
            root / "tmp",
            application_root,
            profile,
            electron_data,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment = self._isolated_environment(root)

        self._download_release(package)
        version = self._run(
            [dpkg_deb, "--field", str(package), "Version"],
            environment=environment,
            label="inspect Joplin Desktop release",
        ).stdout.strip()
        if version != JOPLIN_VERSION:
            raise RuntimeError(f"expected Joplin Desktop {JOPLIN_VERSION}, found {version}")
        self._run(
            [dpkg_deb, "--extract", str(package), str(application_root)],
            environment=environment,
            label="extract Joplin Desktop release",
        )
        executable = application_root / "opt" / "Joplin" / "joplin"
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError("Joplin Desktop release contains no runnable Linux executable")

        port = _free_port()
        settings_file = profile / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "$schema": "https://joplinapp.org/schema/settings.json",
                    "api.port": port,
                    "clipperServer.autoStart": True,
                },
                indent="\t",
            )
            + "\n",
            encoding="utf-8",
        )

        self._xvfb_process, display, xvfb_log = self._start_xvfb(xvfb, root, environment)
        self._log_handles.append(xvfb_log)
        environment["DISPLAY"] = display
        joplin_log_path = root / "joplin.log"
        joplin_log = joplin_log_path.open("w", encoding="utf-8")
        self._log_handles.append(joplin_log)
        self._joplin_process = subprocess.Popen(
            [
                str(executable),
                "--profile",
                str(profile),
                "--no-welcome",
                "--no-sandbox",
                "--disable-gpu",
                f"--user-data-dir={electron_data}",
                "--log-level",
                "error",
            ],
            cwd=root,
            env=environment,
            stdout=joplin_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            self._wait_until_ready(
                self._joplin_process,
                base_url=base_url,
                settings_file=settings_file,
                token_file=token_file,
                log_file=joplin_log_path,
            )
        except Exception:
            self.stop()
            raise
        self.runtime = RunningJoplin(
            version=version,
            base_url=base_url,
            port=port,
            token_file=token_file,
        )
        return self.runtime

    def stop(self) -> None:
        self._stop_process_group(self._joplin_process)
        self._stop_process_group(self._xvfb_process)
        self._joplin_process = None
        self._xvfb_process = None
        for handle in self._log_handles:
            handle.close()
        self._log_handles.clear()
        self.runtime = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def set_running_joplin(runtime: RunningJoplin | None) -> None:
    global _running
    _running = runtime


def running_joplin() -> RunningJoplin:
    if _running is None:
        raise RuntimeError("ephemeral Joplin session fixture is not running")
    return _running
