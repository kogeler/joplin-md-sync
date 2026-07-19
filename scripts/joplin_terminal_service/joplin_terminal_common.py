"""Shared primitives for the standalone Joplin Terminal service tools."""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shlex
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_PORT = 41185
DEFAULT_JOPLIN_VERSION = "latest"
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_VERSION = "latest"
DEFAULT_SYNC_INTERVAL = 300
PING_RESPONSE = "JoplinClipperServer"
SERVICE_NAME = "joplin-terminal.service"
MCP_SERVICE_NAME = "joplin-md-sync-mcp.service"
SUPPORTED_SYNC_INTERVALS = (300, 600, 1800, 3600, 43200, 86400)
SECRET_ENV_NAMES = (
    "JOPLIN_NEXTCLOUD_PASSWORD",
    "JOPLIN_E2EE_PASSWORD",
    "JOPLIN_MCP_AUTH_TOKEN",
    "JOPLIN_TOKEN",
)


class ToolError(RuntimeError):
    """Expected, user-facing service-tool failure."""


class SecretRedactor(logging.Filter):
    """Redact known secrets before a log record reaches any handler."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [value for value in secrets if value]

    def add(self, secret: str | None) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def clear(self) -> None:
        self._secrets.clear()

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = self.redact(record.getMessage())
        record.msg = message
        record.args = ()
        return True


def safe_command(args: Iterable[os.PathLike[str] | str], secrets: Iterable[str] = ()) -> str:
    """Return a shell-like display string with secret values removed."""
    secret_values = tuple(value for value in secrets if value)
    rendered: list[str] = []
    for raw_arg in args:
        arg = os.fspath(raw_arg)
        for secret in secret_values:
            arg = arg.replace(secret, "[REDACTED]")
        rendered.append(arg)
    return shlex.join(rendered)


def child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an external-process environment without installer secrets or JS injection."""
    env = dict(os.environ if source is None else source)
    for name in (*SECRET_ENV_NAMES, "NODE_PATH", "NODE_OPTIONS", "NPM_CONFIG_PREFIX"):
        env.pop(name, None)
    env.update({"LC_ALL": "C", "LANG": "C"})
    return env


def resolved_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def absolute_path(value: str | os.PathLike[str]) -> Path:
    """Return an absolute path without dereferencing executable symlinks."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def default_profile_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = Path(values.get("HOME", str(Path.home()))).expanduser()
    data_home = Path(values.get("XDG_DATA_HOME", str(home / ".local" / "share")))
    return resolved_path(data_home / "joplin-agent" / "profile")


def default_config_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = Path(values.get("HOME", str(Path.home()))).expanduser()
    config_home = Path(values.get("XDG_CONFIG_HOME", str(home / ".config")))
    return resolved_path(config_home / "joplin-agent")


def default_state_dir(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = Path(values.get("HOME", str(Path.home()))).expanduser()
    state_home = Path(values.get("XDG_STATE_HOME", str(home / ".local" / "state")))
    return resolved_path(state_home / "joplin-agent")


def lock_path_for_profile(profile_dir: Path, state_dir: Path) -> Path:
    digest = hashlib.sha256(os.fsencode(profile_dir)).hexdigest()[:16]
    return state_dir / f"profile-{digest}.lock"


class ProfileLock:
    """Non-blocking process lock shared by the installer and supervisor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ToolError(
                f"profile is already in use; could not acquire lock {self.path}"
            ) from None
        os.set_inheritable(fd, False)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> ProfileLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Atomically replace a file with a caller-selected restrictive mode."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(mode)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def systemd_quote(value: str | os.PathLike[str]) -> str:
    """Quote one systemd ExecStart argument and escape specifier expansion."""
    text = os.fspath(value)
    if any(character in text for character in ("\0", "\n", "\r")):
        raise ToolError("systemd arguments may not contain NUL or newline characters")
    text = text.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


@dataclass(frozen=True)
class PingResult:
    ok: bool
    reason: str


def ping_api(port: int, timeout: float) -> PingResult:
    url = f"http://127.0.0.1:{port}/ping"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(256).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        return PingResult(False, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return PingResult(False, "timeout")
        if isinstance(reason, ConnectionRefusedError):
            return PingResult(False, "connection refused")
        return PingResult(False, f"connection error: {type(reason).__name__}")
    except TimeoutError:
        return PingResult(False, "timeout")
    except OSError as exc:
        if isinstance(exc, ConnectionRefusedError):
            return PingResult(False, "connection refused")
        return PingResult(False, f"connection error: {type(exc).__name__}")
    if body != PING_RESPONSE:
        return PingResult(False, "unexpected response")
    return PingResult(True, "ready")


def ping_mcp(port: int, token: str | None, timeout: float) -> PingResult:
    """Check the optionally authenticated MCP endpoint without invoking a tool."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return PingResult(False, f"unexpected HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        code = exc.code
        allow = exc.headers.get("Allow")
        exc.close()
        if code == 405 and allow == "POST":
            return PingResult(True, "ready")
        if code == 401:
            return PingResult(False, "authentication rejected")
        return PingResult(False, f"HTTP {code}")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return PingResult(False, "timeout")
        if isinstance(reason, ConnectionRefusedError):
            return PingResult(False, "connection refused")
        return PingResult(False, f"connection error: {type(reason).__name__}")
    except TimeoutError:
        return PingResult(False, "timeout")
    except OSError as exc:
        if isinstance(exc, ConnectionRefusedError):
            return PingResult(False, "connection refused")
        return PingResult(False, f"connection error: {type(exc).__name__}")
