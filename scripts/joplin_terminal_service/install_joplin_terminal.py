#!/usr/bin/env python3
"""Install rootless Joplin Terminal and the combined joplin-md-sync service."""

from __future__ import annotations

import argparse
import base64
import binascii
import errno
import getpass
import hashlib
import hmac
import json
import logging
import os
import platform
import pty
import pwd
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ASSET_BASE_URL = os.environ.get(
    "JOPLIN_TERMINAL_ASSET_BASE_URL",
    "https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service",
).rstrip("/")


def _load_remote_common() -> None:
    """Load the sibling common module when this installer was downloaded alone."""
    url = f"{ASSET_BASE_URL}/joplin_terminal_common.py"
    try:
        with urllib.request.urlopen(url, timeout=30.0) as response:
            source = response.read(512 * 1024 + 1)
    except (OSError, urllib.error.URLError):
        raise RuntimeError(
            "could not download joplin_terminal_common.py from the project repository"
        ) from None
    if len(source) > 512 * 1024:
        raise RuntimeError("downloaded common module is unexpectedly large")
    module = types.ModuleType("joplin_terminal_common")
    module.__file__ = url
    sys.modules[module.__name__] = module
    exec(compile(source, url, "exec"), module.__dict__)


try:
    import joplin_terminal_common as _common  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "joplin_terminal_common":
        raise
    _load_remote_common()


from joplin_terminal_common import (  # noqa: E402
    ADAPTER_SERVICE_NAME,
    DEFAULT_API_PORT,
    DEFAULT_JOPLIN_VERSION,
    DEFAULT_MCP_PORT,
    DEFAULT_MCP_VERSION,
    DEFAULT_SYNC_INTERVAL,
    SERVICE_NAME,
    SUPPORTED_SYNC_INTERVALS,
    ProfileLock,
    SecretRedactor,
    ToolError,
    absolute_path,
    atomic_write,
    child_environment,
    default_config_dir,
    default_profile_dir,
    default_state_dir,
    lock_path_for_profile,
    ping_api,
    ping_mcp,
    resolved_path,
    safe_command,
    systemd_quote,
)

LOG = logging.getLogger("joplin-terminal-installer")
MINIMUM_NODE_MAJOR = 12
RECOMMENDED_NODE_MAJOR = 18
NPM_TIMEOUT = 900.0
COMMAND_TIMEOUT = 120.0
SYNC_TIMEOUT = 24 * 60 * 60.0
E2EE_TIMEOUT = 24 * 60 * 60.0
LONG_OPERATION_HEARTBEAT = 60.0
MAX_LONG_OUTPUT_BYTES = 256 * 1024
DOWNLOAD_TIMEOUT = 120.0
MAX_RELEASE_FILE_BYTES = 128 * 1024 * 1024
MAX_SERVICE_BEARER_TOKEN_CHARS = 1024
MCP_REPOSITORY = "kogeler/joplin-md-sync"
MCP_RELEASES_URL = os.environ.get(
    "JOPLIN_MD_SYNC_RELEASES_URL",
    f"https://github.com/{MCP_REPOSITORY}/releases",
).rstrip("/")
MCP_RELEASE_API_URL = os.environ.get(
    "JOPLIN_MD_SYNC_RELEASE_API_URL",
    f"https://api.github.com/repos/{MCP_REPOSITORY}/releases/latest",
)
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_PASSWORD_PROMPT_RE = re.compile(rb"(?i)(?:master[ -]?)?password[^\r\n]{0,80}:")


@dataclass(frozen=True)
class InstallPaths:
    prefix: Path
    npm_prefix: Path
    launcher: Path
    profile_dir: Path
    config_dir: Path
    token_file: Path
    mcp_binary: Path
    mcp_config_dir: Path
    mcp_auth_token_file: Path
    gpt_actions_token_file: Path
    state_dir: Path
    lock_file: Path
    deploy_dir: Path
    unit_path: Path
    adapter_unit_path: Path


@dataclass(frozen=True)
class Dependencies:
    node: Path
    npm: Path
    systemctl: Path
    node_version: str
    npm_version: str
    loginctl: Path = Path("/usr/bin/loginctl")


@dataclass
class E2eeResult:
    exit_code: int
    output: bytes
    prompts: int
    timed_out: bool = False


def terminal_input(prompt: str) -> str:
    """Read a non-secret answer from the controlling TTY, not installer stdin."""
    try:
        with open("/dev/tty", "r+b", buffering=0) as terminal:
            terminal.write(prompt.encode("utf-8", errors="replace"))
            answer = terminal.readline()
    except OSError:
        try:
            return input(prompt)
        except EOFError:
            raise ToolError(
                "interactive input is unavailable; run from a terminal or use "
                "--non-interactive with explicit options"
            ) from None
    if not answer:
        raise ToolError(
            "interactive terminal closed; rerun from a terminal or use "
            "--non-interactive with explicit options"
        )
    return answer.decode("utf-8", errors="replace").rstrip("\r\n")


def _env_int(env: Mapping[str, str], name: str, fallback: int) -> int:
    raw = env.get(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise ToolError(f"{name} must be an integer, got {raw!r}") from None


def build_parser(env: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    values = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        description=(
            "Install an isolated Joplin Terminal, configure existing Nextcloud E2EE, "
            "install the released joplin-md-sync service with generated Actions and MCP "
            "credentials, create systemd user services, or purge the complete managed "
            "local installation."
        )
    )
    parser.add_argument("--nextcloud-url", default=values.get("JOPLIN_NEXTCLOUD_URL"))
    parser.add_argument("--nextcloud-user", default=values.get("JOPLIN_NEXTCLOUD_USER"))
    parser.add_argument("--nextcloud-password")
    parser.add_argument("--e2ee-password")
    parser.add_argument(
        "--profile-dir",
        default=values.get("JOPLIN_PROFILE_DIR", str(default_profile_dir(values))),
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=_env_int(values, "JOPLIN_API_PORT", DEFAULT_API_PORT),
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=_env_int(values, "JOPLIN_SYNC_INTERVAL", DEFAULT_SYNC_INTERVAL),
    )
    parser.add_argument(
        "--joplin-version",
        default=values.get("JOPLIN_VERSION", DEFAULT_JOPLIN_VERSION),
    )
    parser.add_argument(
        "--joplin-prefix",
        default=values.get("JOPLIN_INSTALL_PREFIX", "~/.local"),
    )
    parser.add_argument(
        "--joplin-md-sync-version",
        default=values.get("JOPLIN_MD_SYNC_VERSION", DEFAULT_MCP_VERSION),
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=_env_int(values, "JOPLIN_MCP_PORT", DEFAULT_MCP_PORT),
    )
    parser.add_argument(
        "--allow-remote-mcp",
        action="store_true",
        help="bind authenticated MCP to 0.0.0.0 instead of loopback",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "update both Joplin and joplin-md-sync; --joplin-version and "
            "--joplin-md-sync-version independently override latest"
        ),
    )
    parser.add_argument("--skip-e2ee-bootstrap", action="store_true")
    parser.add_argument("--skip-initial-sync", action="store_true")
    parser.add_argument("--no-enable-service", action="store_true")
    parser.add_argument("--no-start-service", action="store_true")
    parser.add_argument("--force-reconfigure", action="store_true")
    parser.add_argument(
        "--enable-linger",
        action="store_true",
        help="enable systemd user lingering with loginctl when it is currently disabled",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="fully uninstall both services and permanently delete all managed local data",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm --purge without an interactive PURGE prompt",
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def build_paths(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> InstallPaths:
    values = os.environ if env is None else env
    prefix = resolved_path(args.joplin_prefix)
    profile_dir = resolved_path(args.profile_dir)
    config_dir = default_config_dir(values)
    state_dir = default_state_dir(values)
    npm_prefix = prefix / "share" / "joplin-agent" / "npm"
    config_home = config_dir.parent
    mcp_config_dir = resolved_path(config_home / "joplin-md-sync")
    return InstallPaths(
        prefix=prefix,
        npm_prefix=npm_prefix,
        launcher=prefix / "bin" / "joplin",
        profile_dir=profile_dir,
        config_dir=config_dir,
        token_file=config_dir / "api-token",
        mcp_binary=prefix / "bin" / "joplin-md-sync",
        mcp_config_dir=mcp_config_dir,
        mcp_auth_token_file=mcp_config_dir / "mcp-token",
        gpt_actions_token_file=mcp_config_dir / "gpt-actions-token",
        state_dir=state_dir,
        lock_file=lock_path_for_profile(profile_dir, state_dir),
        deploy_dir=prefix / "lib" / "joplin-terminal-service",
        unit_path=config_home / "systemd" / "user" / SERVICE_NAME,
        adapter_unit_path=config_home / "systemd" / "user" / ADAPTER_SERVICE_NAME,
    )


def configure_logging(verbose: bool, redactor: SecretRedactor) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(redactor)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def validate_version(value: str, option: str) -> None:
    if value != "latest" and not _VERSION_RE.fullmatch(value):
        raise ToolError(f"{option} must be 'latest' or a concrete version such as 3.6.2")


def validate_nextcloud_url(value: str) -> None:
    split = urllib.parse.urlsplit(value)
    if split.scheme not in ("http", "https") or not split.hostname:
        raise ToolError("--nextcloud-url must be an absolute HTTP(S) URL")
    if split.username or split.password:
        raise ToolError("--nextcloud-url must not contain embedded credentials")
    if split.query or split.fragment:
        raise ToolError("--nextcloud-url must not contain a query string or fragment")
    if split.scheme != "https":
        LOG.warning("Nextcloud URL does not use HTTPS")


def validate_args(args: argparse.Namespace) -> None:
    if args.purge:
        if args.upgrade:
            raise ToolError("--purge cannot be combined with --upgrade")
        return
    if args.yes:
        raise ToolError("--yes is only valid with --purge")
    validate_version(args.joplin_version, "Joplin version")
    validate_version(args.joplin_md_sync_version, "joplin-md-sync version")
    if not 1 <= args.api_port <= 65535:
        raise ToolError("--api-port must be between 1 and 65535")
    if not 1 <= args.mcp_port <= 65535:
        raise ToolError("--mcp-port must be between 1 and 65535")
    if args.api_port == args.mcp_port:
        raise ToolError("--api-port and --mcp-port must be different")
    if args.sync_interval not in SUPPORTED_SYNC_INTERVALS:
        allowed = ", ".join(str(value) for value in SUPPORTED_SYNC_INTERVALS)
        raise ToolError(f"--sync-interval must be one of: {allowed}")
    if args.upgrade:
        return
    if not args.nextcloud_url:
        raise ToolError("--nextcloud-url or JOPLIN_NEXTCLOUD_URL is required")
    if not args.nextcloud_user:
        raise ToolError("--nextcloud-user or JOPLIN_NEXTCLOUD_USER is required")
    validate_nextcloud_url(args.nextcloud_url)


def _append_bounded(buffer: bytearray, chunk: bytes, limit: int) -> int:
    buffer.extend(chunk)
    dropped = max(0, len(buffer) - limit)
    if dropped:
        del buffer[:dropped]
    return dropped


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds_part}s"
    if minutes:
        return f"{minutes}m {seconds_part}s"
    return f"{seconds_part}s"


def _terminate_process_group(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


class CommandRunner:
    def __init__(self, redactor: SecretRedactor, env: Mapping[str, str] | None = None) -> None:
        self.redactor = redactor
        self.env = child_environment(env)

    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        secrets: Sequence[str] = (),
        timeout: float = COMMAND_TIMEOUT,
        check: bool = True,
        sensitive_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [os.fspath(value) for value in args]
        LOG.debug("running: %s", safe_command(command, secrets))
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"command timed out after {timeout:g}s: {safe_command(command, secrets)}"
            ) from None
        except OSError as exc:
            raise ToolError(
                f"could not execute {safe_command(command, secrets)}: {type(exc).__name__}"
            ) from None
        LOG.debug("command exit code: %d", result.returncode)
        if check and result.returncode != 0:
            detail = ""
            if not sensitive_output:
                raw_detail = (result.stderr or result.stdout).strip()[-2000:]
                detail = self.redactor.redact(raw_detail)
            suffix = f": {detail}" if detail else ""
            raise ToolError(
                f"command failed with exit {result.returncode}: "
                f"{safe_command(command, secrets)}{suffix}"
            )
        return result

    def run_long(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        heartbeat_label: str,
        timeout: float,
        heartbeat_interval: float = LONG_OPERATION_HEARTBEAT,
        max_output_bytes: int = MAX_LONG_OUTPUT_BYTES,
        check: bool = True,
        sensitive_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if heartbeat_interval <= 0 or max_output_bytes <= 0:
            raise ValueError("heartbeat interval and output limit must be positive")
        command = [os.fspath(value) for value in args]
        LOG.debug("running long operation: %s", safe_command(command))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolError(
                f"could not execute {safe_command(command)}: {type(exc).__name__}"
            ) from None

        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        for stream, buffer in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, buffer)

        started = time.monotonic()
        deadline = started + timeout
        next_heartbeat = started + heartbeat_interval
        timed_out = False
        try:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    timed_out = True
                    break
                wait = min(0.25, deadline - now, max(0.0, next_heartbeat - now))
                for key, _mask in selector.select(wait):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    _append_bounded(key.data, chunk, max_output_bytes)
                now = time.monotonic()
                if process.poll() is None and now >= next_heartbeat:
                    LOG.info(
                        "%s still running (elapsed %s)",
                        heartbeat_label,
                        _format_elapsed(now - started),
                    )
                    while next_heartbeat <= now:
                        next_heartbeat += heartbeat_interval
        except BaseException:
            _terminate_process_group(process)
            raise
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()

        if timed_out:
            _terminate_process_group(process)
            raise ToolError(
                f"{heartbeat_label} timed out after {_format_elapsed(timeout)}"
            )
        returncode = process.wait()
        result = subprocess.CompletedProcess(
            command,
            returncode,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )
        LOG.debug("long operation exit code: %d", returncode)
        if check and returncode != 0:
            detail = ""
            if not sensitive_output:
                raw_detail = (result.stderr or result.stdout).strip()[-2000:]
                detail = self.redactor.redact(raw_detail)
            suffix = f": {detail}" if detail else ""
            raise ToolError(
                f"{heartbeat_label} failed with exit {returncode}{suffix}"
            )
        return result


def check_dependencies(runner: CommandRunner) -> Dependencies:
    if sys.version_info < (3, 14):
        LOG.warning(
            "Python 3.14 is recommended; continuing with Python %s",
            sys.version.split()[0],
        )
    programs = {name: shutil.which(name) for name in ("node", "npm", "systemctl", "loginctl")}
    missing = [name for name, path in programs.items() if path is None]
    if missing:
        raise ToolError(f"missing required program: {', '.join(missing)}")
    node = absolute_path(programs["node"] or "node")
    npm = absolute_path(programs["npm"] or "npm")
    systemctl = absolute_path(programs["systemctl"] or "systemctl")
    loginctl = absolute_path(programs["loginctl"] or "loginctl")
    node_result = runner.run([node, "--version"])
    npm_result = runner.run([npm, "--version"])
    node_version = node_result.stdout.strip().lstrip("v")
    npm_version = npm_result.stdout.strip()
    try:
        node_major = int(node_version.split(".", 1)[0])
    except ValueError:
        raise ToolError(f"could not parse Node.js version from {node}: {node_version!r}") from None
    if node_major < MINIMUM_NODE_MAJOR:
        raise ToolError(f"Joplin requires Node.js {MINIMUM_NODE_MAJOR}+; found {node_version}")
    if node_major < RECOMMENDED_NODE_MAJOR:
        LOG.warning(
            "Node.js %s satisfies upstream minimum but is obsolete; use an active LTS release",
            node_version,
        )
    LOG.info("Python %s", sys.version.split()[0])
    LOG.info("Node.js %s; npm %s", node_version, npm_version)
    return Dependencies(node, npm, systemctl, node_version, npm_version, loginctl)


def parse_npm_version(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        return None
    package = dependencies.get("joplin")
    if not isinstance(package, dict):
        return None
    version = package.get("version")
    return version if isinstance(version, str) and version else None


def installed_joplin_version(runner: CommandRunner, npm: Path, prefix: Path) -> str | None:
    result = runner.run(
        [npm, "list", "--global", "--prefix", prefix, "joplin", "--depth=0", "--json"],
        check=False,
    )
    return parse_npm_version(result.stdout)


def resolve_latest_version(runner: CommandRunner, npm: Path) -> str:
    result = runner.run([npm, "view", "joplin", "version", "--json"], timeout=NPM_TIMEOUT)
    try:
        version = json.loads(result.stdout)
    except json.JSONDecodeError:
        version = result.stdout.strip().strip('"')
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ToolError("npm returned an invalid latest Joplin version")
    return version


def _launcher_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def _safe_launcher_state(paths: InstallPaths) -> str:
    if not paths.launcher.exists() and not paths.launcher.is_symlink():
        return "missing"
    if not paths.launcher.is_symlink():
        raise ToolError(f"refusing to overwrite non-symlink launcher: {paths.launcher}")
    target = _launcher_target(paths.launcher)
    isolated = (paths.npm_prefix / "bin" / "joplin").resolve(strict=False)
    legacy = (paths.prefix / "lib" / "node_modules" / "joplin" / "main.js").resolve(strict=False)
    if target == isolated or target == (
        paths.npm_prefix / "lib" / "node_modules" / "joplin" / "main.js"
    ).resolve(strict=False):
        return "isolated"
    if target == legacy:
        return "legacy"
    raise ToolError(f"refusing to overwrite unknown Joplin launcher {paths.launcher} -> {target}")


def install_launcher(paths: InstallPaths) -> None:
    source = paths.npm_prefix / "bin" / "joplin"
    if not source.exists():
        raise ToolError(f"npm did not create the expected Joplin executable: {source}")
    paths.launcher.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    relative_target = os.path.relpath(source, paths.launcher.parent)
    temporary = paths.launcher.parent / f".{paths.launcher.name}.{os.getpid()}.tmp"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(relative_target)
    os.replace(temporary, paths.launcher)


def smoke_test_joplin(runner: CommandRunner, joplin: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="joplin-terminal-smoke-") as temporary:
        profile = Path(temporary) / "profile"
        server = runner.run([joplin, "--profile", profile, "help", "server"])
        e2ee = runner.run([joplin, "--profile", profile, "help", "e2ee"])
    if "server <command>" not in server.stdout or "e2ee <command>" not in e2ee.stdout:
        raise ToolError("installed Joplin does not expose the required server/E2EE commands")


def install_or_update_joplin(
    runner: CommandRunner,
    dependencies: Dependencies,
    paths: InstallPaths,
    requested_version: str,
) -> str:
    launcher_state = _safe_launcher_state(paths)
    desired = (
        resolve_latest_version(runner, dependencies.npm)
        if requested_version == "latest"
        else requested_version
    )
    current = installed_joplin_version(runner, dependencies.npm, paths.npm_prefix)
    legacy = installed_joplin_version(runner, dependencies.npm, paths.prefix)
    if current == desired:
        LOG.info("isolated Joplin %s is already installed", desired)
    else:
        action = "installing" if current is None else f"updating from {current} to"
        LOG.info("%s isolated Joplin %s", action, desired)
        runner.run(
            [
                dependencies.npm,
                "install",
                "--global",
                "--prefix",
                paths.npm_prefix,
                f"joplin@{desired}",
            ],
            timeout=NPM_TIMEOUT,
        )
    actual = installed_joplin_version(runner, dependencies.npm, paths.npm_prefix)
    if actual != desired:
        raise ToolError(f"expected isolated Joplin {desired}, found {actual or 'nothing'}")
    isolated_executable = paths.npm_prefix / "bin" / "joplin"
    smoke_test_joplin(runner, isolated_executable)
    if legacy is not None:
        if launcher_state not in ("legacy", "missing"):
            raise ToolError(
                "legacy npm Joplin exists but the stable launcher is not legacy-managed"
            )
        LOG.info("removing legacy non-isolated Joplin %s from %s", legacy, paths.prefix)
        runner.run(
            [dependencies.npm, "uninstall", "--global", "--prefix", paths.prefix, "joplin"],
            timeout=NPM_TIMEOUT,
        )
    install_launcher(paths)
    LOG.info("Joplin launcher: %s", paths.launcher)
    return actual


def mcp_asset_name(
    system: str | None = None,
    machine: str | None = None,
) -> str:
    current_system = sys.platform if system is None else system
    current_machine = platform.machine().lower() if machine is None else machine.lower()
    architectures = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    architecture = architectures.get(current_machine)
    if current_system != "linux" or architecture is None:
        raise ToolError(
            "no joplin-md-sync standalone release for "
            f"{current_system}/{current_machine or 'unknown'}"
        )
    return f"joplin-md-sync-linux-{architecture}"


def fetch_release_bytes(url: str, *, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "joplin-terminal-installer"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            data = response.read(limit + 1)
    except (OSError, urllib.error.URLError):
        raise ToolError("could not download the requested GitHub release metadata") from None
    if len(data) > limit:
        raise ToolError("downloaded GitHub release metadata is unexpectedly large")
    return data


def resolve_latest_mcp_version() -> str:
    try:
        payload = json.loads(fetch_release_bytes(MCP_RELEASE_API_URL, limit=2 * 1024 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError("GitHub returned invalid latest-release metadata") from None
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else ""
    if not _VERSION_RE.fullmatch(version):
        raise ToolError("GitHub latest release does not have a valid vX.Y.Z tag")
    return version


def parse_release_checksum(contents: bytes, asset_name: str) -> str:
    try:
        lines = contents.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise ToolError("release checksum file is not ASCII") from None
    for line in lines:
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        digest, filename = fields
        if filename.lstrip("*") != asset_name:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
        break
    raise ToolError(f"release checksum file has no valid entry for {asset_name}")


def mcp_binary_version(runner: CommandRunner, binary: Path) -> str:
    result = runner.run([binary, "version", "--json"], check=False)
    if result.returncode != 0:
        detail = runner.redactor.redact((result.stderr or result.stdout).strip()[-1000:])
        suffix = f": {detail}" if detail else ""
        raise ToolError(
            f"refusing non-working joplin-md-sync executable: {binary}{suffix}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ToolError(f"refusing unrecognised joplin-md-sync executable: {binary}") from None
    if not isinstance(payload, dict):
        raise ToolError(f"refusing unrecognised joplin-md-sync executable: {binary}")
    version = payload.get("tool_version")
    if (
        payload.get("repository") != f"https://github.com/{MCP_REPOSITORY}"
        or payload.get("distribution") != "standalone"
        or not isinstance(version, str)
        or not _VERSION_RE.fullmatch(version)
    ):
        raise ToolError(f"refusing non-standalone joplin-md-sync executable: {binary}")
    return version


def smoke_test_mcp_binary(runner: CommandRunner, binary: Path) -> None:
    result = runner.run([binary, "capabilities", "--json"])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ToolError("joplin-md-sync capabilities output is invalid") from None
    commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(commands, list) or "mcp serve" not in commands:
        raise ToolError("joplin-md-sync standalone does not provide mcp serve")


def installed_mcp_version(runner: CommandRunner, binary: Path) -> str | None:
    if not binary.exists() and not binary.is_symlink():
        return None
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ToolError(f"refusing to overwrite non-executable path: {binary}")
    version = mcp_binary_version(runner, binary)
    smoke_test_mcp_binary(runner, binary)
    return version


def download_mcp_binary(
    runner: CommandRunner,
    paths: InstallPaths,
    version: str,
    asset_name: str,
) -> Path:
    release_url = f"{MCP_RELEASES_URL}/download/v{version}"
    checksum = parse_release_checksum(
        fetch_release_bytes(f"{release_url}/SHA256SUMS.txt", limit=2 * 1024 * 1024),
        asset_name,
    )
    paths.mcp_binary.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".joplin-md-sync.",
        dir=paths.mcp_binary.parent,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(
        f"{release_url}/{asset_name}",
        headers={"User-Agent": "joplin-terminal-installer"},
    )
    try:
        os.fchmod(fd, 0o700)
        with os.fdopen(fd, "wb") as output:
            try:
                with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_RELEASE_FILE_BYTES:
                            raise ToolError("joplin-md-sync release asset is unexpectedly large")
                        digest.update(chunk)
                        output.write(chunk)
            except (OSError, urllib.error.URLError):
                raise ToolError(f"could not download release asset {asset_name}") from None
            output.flush()
            os.fsync(output.fileno())
        if not hmac.compare_digest(digest.hexdigest(), checksum):
            raise ToolError(f"SHA-256 mismatch for release asset {asset_name}")
        actual = mcp_binary_version(runner, temporary)
        if actual != version:
            raise ToolError(f"expected joplin-md-sync {version}, downloaded {actual}")
        smoke_test_mcp_binary(runner, temporary)
        return temporary
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def install_or_update_mcp(
    runner: CommandRunner,
    paths: InstallPaths,
    requested_version: str,
) -> str:
    desired = resolve_latest_mcp_version() if requested_version == "latest" else requested_version
    current = installed_mcp_version(runner, paths.mcp_binary)
    if current == desired:
        LOG.info("joplin-md-sync %s is already installed", desired)
        return desired
    action = "installing" if current is None else f"updating from {current} to"
    LOG.info("%s joplin-md-sync %s", action, desired)
    temporary = download_mcp_binary(runner, paths, desired, mcp_asset_name())
    try:
        os.replace(temporary, paths.mcp_binary)
        paths.mcp_binary.chmod(0o700)
    finally:
        temporary.unlink(missing_ok=True)
    LOG.info("joplin-md-sync executable: %s", paths.mcp_binary)
    return desired


def validate_service_bearer_token(token: str, *, label: str) -> None:
    if not token or len(token) > MAX_SERVICE_BEARER_TOKEN_CHARS:
        raise ToolError(
            f"{label} bearer token must contain at most "
            f"{MAX_SERVICE_BEARER_TOKEN_CHARS} characters"
        )
    if re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", token) is None:
        raise ToolError(f"{label} bearer token must use URL-safe Base64 encoding")
    try:
        encoded = token.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise ToolError(f"{label} bearer token must use URL-safe Base64 encoding") from None
    if len(decoded) < 32:
        raise ToolError(f"{label} bearer token must encode at least 32 bytes")


def constant_time_token_equal(left: str, right: str) -> bool:
    """Compare ASCII token values without raising on corrupted local input."""
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeEncodeError:
        return False


def validate_mcp_auth_token(token: str) -> None:
    validate_service_bearer_token(token, label="MCP")


def load_mcp_auth_token(paths: InstallPaths, redactor: SecretRedactor) -> str | None:
    if not paths.mcp_auth_token_file.exists():
        return None
    if paths.mcp_auth_token_file.is_symlink() or not paths.mcp_auth_token_file.is_file():
        raise ToolError(f"MCP auth token path is not a file: {paths.mcp_auth_token_file}")
    if paths.mcp_auth_token_file.stat().st_size > MAX_SERVICE_BEARER_TOKEN_CHARS + 2:
        raise ToolError("existing MCP auth token file is too large")
    try:
        token = paths.mcp_auth_token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("existing MCP auth token file cannot be read") from None
    validate_mcp_auth_token(token)
    paths.mcp_auth_token_file.chmod(0o600)
    redactor.add(token)
    return token


def resolve_mcp_auth_token(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> str:
    if paths.mcp_auth_token_file.exists() and not paths.mcp_auth_token_file.is_file():
        raise ToolError(f"MCP auth token path is not a file: {paths.mcp_auth_token_file}")
    if paths.mcp_auth_token_file.is_file():
        token = load_mcp_auth_token(paths, redactor)
        if token is None:  # guarded by is_file(); keeps the type explicit
            raise ToolError("existing MCP auth token file cannot be read")
        LOG.info("preserving existing MCP bearer token")
        return token
    token = secrets.token_urlsafe(32)
    validate_mcp_auth_token(token)
    redactor.add(token)
    LOG.info("generated a new MCP bearer token")
    return token


def configure_mcp_auth_token(paths: InstallPaths, token: str) -> None:
    validate_mcp_auth_token(token)
    content = f"{token}\n".encode()
    if paths.mcp_auth_token_file.is_file() and paths.mcp_auth_token_file.read_bytes() == content:
        paths.mcp_auth_token_file.chmod(0o600)
        LOG.info("protected MCP bearer token is unchanged")
        return
    atomic_write(paths.mcp_auth_token_file, content, 0o600)
    LOG.info("stored protected MCP bearer token")


def validate_gpt_actions_token(token: str) -> None:
    validate_service_bearer_token(token, label="GPT Actions")


def load_gpt_actions_token(paths: InstallPaths, redactor: SecretRedactor) -> str | None:
    if not paths.gpt_actions_token_file.exists():
        return None
    if paths.gpt_actions_token_file.is_symlink() or not paths.gpt_actions_token_file.is_file():
        raise ToolError(
            f"GPT Actions token path is not a file: {paths.gpt_actions_token_file}"
        )
    if paths.gpt_actions_token_file.stat().st_size > MAX_SERVICE_BEARER_TOKEN_CHARS + 2:
        raise ToolError("existing GPT Actions token file is too large")
    try:
        token = paths.gpt_actions_token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("existing GPT Actions token file cannot be read") from None
    validate_gpt_actions_token(token)
    paths.gpt_actions_token_file.chmod(0o600)
    redactor.add(token)
    return token


def resolve_gpt_actions_token(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> str:
    if paths.gpt_actions_token_file.exists() and not paths.gpt_actions_token_file.is_file():
        raise ToolError(
            f"GPT Actions token path is not a file: {paths.gpt_actions_token_file}"
        )
    if paths.gpt_actions_token_file.is_file():
        token = load_gpt_actions_token(paths, redactor)
        if token is None:  # guarded by is_file(); keeps the type explicit
            raise ToolError("existing GPT Actions token file cannot be read")
        LOG.info("preserving existing GPT Actions bearer token")
        return token
    token = secrets.token_urlsafe(32)
    validate_gpt_actions_token(token)
    redactor.add(token)
    LOG.info("generated a new GPT Actions bearer token")
    return token


def configure_gpt_actions_token(paths: InstallPaths, token: str) -> None:
    validate_gpt_actions_token(token)
    content = f"{token}\n".encode()
    if (
        paths.gpt_actions_token_file.is_file()
        and paths.gpt_actions_token_file.read_bytes() == content
    ):
        paths.gpt_actions_token_file.chmod(0o600)
        LOG.info("protected GPT Actions bearer token is unchanged")
        return
    atomic_write(paths.gpt_actions_token_file, content, 0o600)
    LOG.info("stored protected GPT Actions bearer token")


def resolve_service_tokens(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> tuple[str, str]:
    mcp_auth_token = resolve_mcp_auth_token(paths, redactor)
    gpt_actions_token = resolve_gpt_actions_token(paths, redactor)
    if constant_time_token_equal(mcp_auth_token, gpt_actions_token):
        raise ToolError("GPT Actions and MCP bearer tokens must be different")
    return mcp_auth_token, gpt_actions_token


def validate_distinct_service_tokens(
    paths: InstallPaths,
    gpt_actions_token: str,
    mcp_auth_token: str,
) -> None:
    if constant_time_token_equal(gpt_actions_token, mcp_auth_token):
        raise ToolError("GPT Actions and MCP bearer tokens must be different")
    try:
        joplin_token = paths.token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("Joplin API token file cannot be read") from None
    try:
        if constant_time_token_equal(gpt_actions_token, joplin_token):
            raise ToolError("GPT Actions and Joplin API tokens must be different")
        if constant_time_token_equal(mcp_auth_token, joplin_token):
            raise ToolError("MCP and Joplin API tokens must be different")
    finally:
        del joplin_token


def report_service_token_paths(paths: InstallPaths) -> None:
    LOG.info("service bearer token files (token values are not printed):")
    LOG.info("GPT Actions: %s", paths.gpt_actions_token_file)
    LOG.info("MCP: %s", paths.mcp_auth_token_file)


def service_active(
    runner: CommandRunner,
    systemctl: Path,
    service_name: str = SERVICE_NAME,
) -> bool:
    result = runner.run(
        [systemctl, "--user", "is-active", service_name],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip() == "active":
        return True
    combined = f"{result.stdout}\n{result.stderr}"
    if "Failed to connect to bus" in combined or "No medium found" in combined:
        raise ToolError("systemd user bus is unavailable")
    return False


def systemctl_command(
    runner: CommandRunner, systemctl: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return runner.run([systemctl, "--user", *arguments])


def purge_systemctl_path() -> Path:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise ToolError("missing required program: systemctl")
    return absolute_path(systemctl)


def confirm_purge(
    args: argparse.Namespace,
    paths: InstallPaths,
    *,
    input_fn: Callable[[str], str] = terminal_input,
) -> None:
    if args.yes:
        return
    if args.non_interactive:
        raise ToolError("--purge in non-interactive mode requires --yes")
    answer = input_fn(
        f"Permanently delete the local Joplin profile {paths.profile_dir}? Type PURGE: "
    )
    if answer != "PURGE":
        raise ToolError("purge cancelled")


def validate_purge_paths(paths: InstallPaths, env: Mapping[str, str]) -> None:
    home = resolved_path(env.get("HOME", str(Path.home())))
    if paths.prefix == home or not paths.prefix.is_relative_to(home):
        raise ToolError(f"refusing to purge a non-user installation prefix: {paths.prefix}")
    directories = {
        "npm prefix": paths.npm_prefix,
        "profile": paths.profile_dir,
        "Joplin config": paths.config_dir,
        "joplin-md-sync config": paths.mcp_config_dir,
        "state": paths.state_dir,
        "supervisor": paths.deploy_dir,
    }
    filesystem_root = Path(paths.profile_dir.anchor or "/")
    for label, path in directories.items():
        if path in (filesystem_root, home) or len(path.parts) < 3:
            raise ToolError(f"refusing to purge unsafe {label} path: {path}")
    for label, path in directories.items():
        if label != "state" and paths.state_dir.is_relative_to(path):
            raise ToolError(f"refusing to purge {label} because it contains the profile lock state")


def managed_purge_paths(paths: InstallPaths) -> tuple[Path, ...]:
    backups = tuple(paths.unit_path.parent.glob(f"{SERVICE_NAME}.bak-*")) + tuple(
        paths.adapter_unit_path.parent.glob(f"{ADAPTER_SERVICE_NAME}.bak-*")
    )
    return (
        paths.unit_path,
        paths.adapter_unit_path,
        *backups,
        paths.launcher,
        paths.mcp_binary,
        paths.npm_prefix,
        paths.profile_dir,
        paths.config_dir,
        paths.mcp_config_dir,
        paths.deploy_dir,
    )


def remove_managed_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    LOG.info("removing managed path: %s", path)
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            raise ToolError(f"refusing to remove unsupported managed path: {path}")
    except OSError as exc:
        raise ToolError(f"could not remove managed path {path}: {exc.strerror}") from None


def stop_services_for_purge(runner: CommandRunner, systemctl: Path) -> None:
    result = runner.run(
        [
            systemctl,
            "--user",
            "disable",
            "--now",
            ADAPTER_SERVICE_NAME,
            SERVICE_NAME,
        ],
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    if "Failed to connect to bus" in combined or "No medium found" in combined:
        raise ToolError("systemd user bus is unavailable; services were not purged")
    for service_name in (ADAPTER_SERVICE_NAME, SERVICE_NAME):
        state = runner.run(
            [systemctl, "--user", "is-active", service_name],
            check=False,
        )
        if state.returncode == 0:
            raise ToolError(f"refusing to purge while {service_name} is still active")


def purge_installation(
    runner: CommandRunner,
    systemctl: Path,
    paths: InstallPaths,
    env: Mapping[str, str],
) -> None:
    validate_purge_paths(paths, env)
    _safe_launcher_state(paths)
    stop_services_for_purge(runner, systemctl)
    with ProfileLock(paths.lock_file):
        for path in managed_purge_paths(paths):
            remove_managed_path(path)
    remove_managed_path(paths.state_dir)
    for parent in {paths.npm_prefix.parent, paths.profile_dir.parent}:
        try:
            parent.rmdir()
        except OSError:
            pass
    runner.run([systemctl, "--user", "daemon-reload"])
    LOG.info("full local Joplin Terminal and MCP purge completed")


def current_username() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        raise ToolError(f"could not resolve a username for uid {os.getuid()}") from None


def lingering_enabled(runner: CommandRunner, loginctl: Path, username: str) -> bool:
    result = runner.run(
        [loginctl, "show-user", username, "--property=Linger", "--value"],
        check=False,
    )
    if result.returncode != 0:
        raise ToolError(f"could not query systemd lingering for user {username}")
    value = result.stdout.strip().lower()
    if value not in ("yes", "no"):
        raise ToolError(f"loginctl returned an invalid Linger value for user {username}")
    return value == "yes"


def configure_lingering(
    runner: CommandRunner,
    loginctl: Path,
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = terminal_input,
) -> bool:
    username = current_username()
    if lingering_enabled(runner, loginctl, username):
        LOG.info("systemd user lingering is enabled for %s", username)
        return True
    enable = args.enable_linger
    if not enable and not args.non_interactive:
        answer = input_fn(
            "Systemd user lingering is disabled. Enable it so services survive logout? [y/N] "
        )
        enable = answer.strip().lower() in ("y", "yes")
    if not enable:
        LOG.warning(
            "systemd user lingering is disabled; services may stop after logout. "
            "Run: loginctl enable-linger %s",
            username,
        )
        return False
    command = [loginctl, "enable-linger", username]
    LOG.info("enabling systemd user lingering: %s", safe_command(command))
    runner.run(command)
    if not lingering_enabled(runner, loginctl, username):
        raise ToolError(f"systemd user lingering remains disabled for user {username}")
    LOG.info("systemd user lingering is enabled for %s", username)
    return True


def parse_setting(output: str, name: str) -> str:
    prefix = f"{name} ="
    for line in output.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()[len(prefix) :].strip()
    raise ToolError(f"Joplin did not return setting {name}")


def joplin_command(paths: InstallPaths, *arguments: str) -> list[str]:
    return [str(paths.launcher), "--profile", str(paths.profile_dir), *arguments]


def read_setting(
    runner: CommandRunner,
    paths: InstallPaths,
    name: str,
    *,
    sensitive: bool = False,
) -> str:
    result = runner.run(
        joplin_command(paths, "config", name),
        sensitive_output=sensitive,
    )
    return parse_setting(result.stdout, name)


def write_setting(
    runner: CommandRunner,
    paths: InstallPaths,
    name: str,
    value: str,
    *,
    secret: bool = False,
) -> None:
    runner.run(
        joplin_command(paths, "config", name, value),
        secrets=(value,) if secret else (),
        sensitive_output=secret,
    )


def confirm_reconfigure(
    args: argparse.Namespace,
    current_target: str,
    current_url: str,
    input_fn: Callable[[str], str] = terminal_input,
) -> None:
    target_mismatch = current_target not in ("0", "5", "null", "")
    url_mismatch = bool(current_url and current_url != "null") and (
        current_url.rstrip("/") != args.nextcloud_url.rstrip("/")
    )
    if not (target_mismatch or url_mismatch):
        return
    LOG.warning(
        "profile uses sync target %s and Nextcloud URL %s",
        current_target,
        current_url or "(unset)",
    )
    if args.force_reconfigure:
        return
    if args.non_interactive:
        raise ToolError("existing sync target/URL differs; pass --force-reconfigure to change it")
    answer = input_fn("Reconfigure this profile for the requested Nextcloud target? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        raise ToolError("configuration change cancelled")


def available_secret(
    cli_value: str | None,
    environment_name: str,
    env: Mapping[str, str],
) -> str | None:
    if cli_value is not None:
        return cli_value
    return env.get(environment_name)


def resolve_secret(
    cli_value: str | None,
    environment_name: str,
    prompt: str,
    *,
    env: Mapping[str, str],
    non_interactive: bool,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> str:
    value = available_secret(cli_value, environment_name, env)
    if value:
        return value
    if non_interactive:
        raise ToolError(
            f"required secret is missing; pass its CLI option or set {environment_name}"
        )
    try:
        value = getpass_fn(prompt)
    except (EOFError, OSError):
        raise ToolError(
            f"interactive input for {environment_name} is unavailable; "
            "pass its CLI option or set the environment variable"
        ) from None
    if not value:
        raise ToolError(f"{prompt.strip(': ')} may not be empty")
    return value


def configure_profile(
    runner: CommandRunner,
    paths: InstallPaths,
    args: argparse.Namespace,
    nextcloud_password: str,
    *,
    input_fn: Callable[[str], str] = terminal_input,
) -> None:
    paths.profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.profile_dir.chmod(0o700)
    current_target = read_setting(runner, paths, "sync.target").split(maxsplit=1)[0]
    current_url = read_setting(runner, paths, "sync.5.path")
    confirm_reconfigure(args, current_target, current_url, input_fn)
    LOG.info("configuring Nextcloud target and recurrent sync")
    write_setting(runner, paths, "sync.target", "5")
    write_setting(runner, paths, "sync.5.path", args.nextcloud_url)
    write_setting(runner, paths, "sync.5.username", args.nextcloud_user)
    write_setting(
        runner,
        paths,
        "sync.5.password",
        nextcloud_password,
        secret=True,
    )
    write_setting(runner, paths, "sync.interval", str(args.sync_interval))
    write_setting(runner, paths, "api.port", str(args.api_port))


def run_sync(runner: CommandRunner, paths: InstallPaths, label: str) -> None:
    LOG.info("%s", label)
    runner.run_long(
        joplin_command(paths, "sync"),
        heartbeat_label=label,
        timeout=SYNC_TIMEOUT,
        sensitive_output=True,
    )


def _terminate_pty_child(pid: int, timeout: float = 5.0) -> int:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _waited, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def run_e2ee_decrypt_pty(
    joplin: Path,
    profile_dir: Path,
    password: str,
    *,
    timeout: float = E2EE_TIMEOUT,
    heartbeat_interval: float = LONG_OPERATION_HEARTBEAT,
    max_output_bytes: int = MAX_LONG_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> E2eeResult:
    if heartbeat_interval <= 0 or max_output_bytes <= 0:
        raise ValueError("heartbeat interval and output limit must be positive")
    child_env = child_environment(env)
    child_env.update({"TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24"})
    pid, fd = pty.fork()
    if pid == 0:
        argv = [
            str(joplin),
            "--profile",
            str(profile_dir),
            "e2ee",
            "decrypt",
            "--retry-failed-items",
        ]
        os.execve(str(joplin), argv, child_env)
    os.set_blocking(fd, False)
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    output = bytearray()
    password_bytes = bytearray(password.encode("utf-8"))
    prompt_scan_start = 0
    prompts = 0
    started = time.monotonic()
    deadline = started + timeout
    next_heartbeat = started + heartbeat_interval
    exit_code: int | None = None

    def redacted_output() -> bytes:
        return bytes(output).replace(bytes(password_bytes), b"[REDACTED]")

    try:
        while time.monotonic() < deadline:
            for _key, _mask in selector.select(0.1):
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    chunk = chunk.replace(bytes(password_bytes), b"[REDACTED]")
                    dropped = _append_bounded(output, chunk, max_output_bytes)
                    prompt_scan_start = max(0, prompt_scan_start - dropped)
            match = _PASSWORD_PROMPT_RE.search(output, prompt_scan_start)
            if match:
                os.write(fd, password_bytes + b"\r")
                prompts += 1
                prompt_scan_start = match.end()
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                exit_code = os.waitstatus_to_exitcode(status)
                break
            now = time.monotonic()
            if now >= next_heartbeat:
                LOG.info(
                    "E2EE decryption still running (elapsed %s)",
                    _format_elapsed(now - started),
                )
                while next_heartbeat <= now:
                    next_heartbeat += heartbeat_interval
        if exit_code is None:
            _terminate_pty_child(pid)
            return E2eeResult(124, redacted_output(), prompts, timed_out=True)
        return E2eeResult(exit_code, redacted_output(), prompts)
    finally:
        for index in range(len(password_bytes)):
            password_bytes[index] = 0
        os.close(fd)


def e2ee_status_enabled(runner: CommandRunner, paths: InstallPaths) -> bool:
    result = runner.run(joplin_command(paths, "e2ee", "status"), check=False)
    return result.returncode == 0 and b"Encryption is: Enabled" in result.stdout.encode()


def persisted_e2ee_works(runner: CommandRunner, paths: InstallPaths) -> bool:
    result = runner.run_long(
        joplin_command(
            paths,
            "e2ee",
            "decrypt",
            "--retry-failed-items",
            "--force",
        ),
        heartbeat_label="E2EE persistence verification",
        check=False,
        timeout=E2EE_TIMEOUT,
        sensitive_output=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "Completed decryption." in output


def manual_e2ee_error(paths: InstallPaths, detail: str) -> ToolError:
    command = safe_command(joplin_command(paths, "e2ee", "decrypt", "--retry-failed-items"))
    return ToolError(
        f"{detail}. Run this command manually: {command}; then rerun the installer "
        "with --skip-e2ee-bootstrap"
    )


def bootstrap_e2ee(
    runner: CommandRunner,
    paths: InstallPaths,
    args: argparse.Namespace,
    redactor: SecretRedactor,
    env: Mapping[str, str],
    *,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> None:
    if not e2ee_status_enabled(runner, paths):
        raise manual_e2ee_error(
            paths,
            "sync target did not report E2EE enabled; no new master key was created",
        )
    if persisted_e2ee_works(runner, paths):
        LOG.info("existing E2EE key cache is valid; bootstrap is not repeated")
    elif args.skip_e2ee_bootstrap:
        raise manual_e2ee_error(paths, "stored E2EE key password is not usable")
    else:
        password = resolve_secret(
            args.e2ee_password,
            "JOPLIN_E2EE_PASSWORD",
            "Joplin E2EE password: ",
            env=env,
            non_interactive=args.non_interactive,
            getpass_fn=getpass_fn,
        )
        redactor.add(password)
        LOG.info("unlocking existing E2EE master keys")
        result = run_e2ee_decrypt_pty(paths.launcher, paths.profile_dir, password)
        del password
        lowered = result.output.lower()
        if result.timed_out:
            raise manual_e2ee_error(paths, "E2EE decryption timed out")
        if b"invalid password" in lowered:
            raise ToolError("Joplin rejected the E2EE password")
        if result.exit_code != 0:
            raise manual_e2ee_error(
                paths, f"E2EE decrypt command failed with exit {result.exit_code}"
            )
        if result.prompts < 1:
            raise manual_e2ee_error(paths, "Joplin did not present a recognised password prompt")
        if b"completed decryption." not in lowered:
            raise manual_e2ee_error(paths, "Joplin did not confirm completed decryption")
        if not persisted_e2ee_works(runner, paths):
            raise ToolError("E2EE password did not remain usable in a fresh Joplin process")
        LOG.info("E2EE keys unlocked and encrypted items decrypted")

    # The command output contains notebook names, so it is captured and never logged.
    runner.run(joplin_command(paths, "status"), sensitive_output=True)
    LOG.info("Joplin can read synced metadata without decryption errors")


def extract_api_token(
    runner: CommandRunner,
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> None:
    token = read_setting(runner, paths, "api.token", sensitive=True)
    if token in ("", "null") or len(token) < 32 or any(character.isspace() for character in token):
        raise ToolError("Joplin did not provide a valid api.token")
    redactor.add(token)
    atomic_write(paths.token_file, f"{token}\n".encode(), 0o600)
    del token
    LOG.info("protected Joplin API token file: %s", paths.token_file)


def read_project_asset(relative_path: str, *, limit: int = 2 * 1024 * 1024) -> bytes:
    """Read a local sibling asset or fetch it for a single-file installer run."""
    local_path = Path(__file__).resolve().parent / relative_path
    if local_path.is_file():
        return local_path.read_bytes()
    url = f"{ASSET_BASE_URL}/{urllib.parse.quote(relative_path, safe='/')}"
    LOG.info("downloading installer asset: %s", relative_path)
    try:
        with urllib.request.urlopen(url, timeout=30.0) as response:
            data = response.read(limit + 1)
    except (OSError, urllib.error.URLError):
        raise ToolError(f"could not download installer asset {relative_path}") from None
    if len(data) > limit:
        raise ToolError(f"downloaded installer asset is unexpectedly large: {relative_path}")
    return data


def deploy_supervisor(paths: InstallPaths) -> tuple[Path, Path]:
    paths.deploy_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.deploy_dir.chmod(0o700)
    runner_target = paths.deploy_dir / "run_joplin_terminal.py"
    common_target = paths.deploy_dir / "joplin_terminal_common.py"
    atomic_write(runner_target, read_project_asset("run_joplin_terminal.py"), 0o700)
    atomic_write(common_target, read_project_asset("joplin_terminal_common.py"), 0o600)
    return runner_target, common_target


def render_unit(
    template: str,
    *,
    python: Path,
    node: Path,
    supervisor: Path,
    paths: InstallPaths,
    api_port: int,
    sync_interval: int,
) -> str:
    arguments: list[str | os.PathLike[str]] = [
        python,
        supervisor,
        "--node-path",
        node,
        "--joplin-path",
        paths.launcher,
        "--profile-dir",
        paths.profile_dir,
        "--lock-file",
        paths.lock_file,
        "--api-port",
        str(api_port),
        "--sync-interval",
        str(sync_interval),
    ]
    exec_start = " ".join(systemd_quote(argument) for argument in arguments)
    read_write_paths = " ".join(
        systemd_quote(path) for path in (paths.profile_dir, paths.lock_file.parent)
    )
    if node_uses_snap_dispatcher(node):
        # snap-confine cannot resolve its canonical mount from a systemd mount
        # namespace. Keep the stable /snap/bin alias and retain non-mount limits.
        mount_namespace_hardening = "\n".join(
            (
                "PrivateDevices=false",
                "PrivateTmp=false",
                "ProtectControlGroups=false",
                "ProtectKernelModules=false",
                "ProtectKernelTunables=false",
                "ProtectSystem=false",
            )
        )
    else:
        mount_namespace_hardening = "\n".join(
            (
                "PrivateDevices=true",
                "PrivateTmp=true",
                "ProtectControlGroups=true",
                "ProtectKernelModules=true",
                "ProtectKernelTunables=true",
                "ProtectSystem=strict",
                f"ReadWritePaths={read_write_paths}",
            )
        )
    return template.format(
        exec_start=exec_start,
        mount_namespace_hardening=mount_namespace_hardening,
    )


def node_uses_snap_dispatcher(node: Path) -> bool:
    node = absolute_path(node)
    if node.parent == Path("/snap/bin"):
        return True
    try:
        return node.is_symlink() and node.resolve(strict=True).name == "snap"
    except OSError:
        return False


def render_adapter_unit(
    template: str,
    *,
    paths: InstallPaths,
    api_port: int,
    mcp_port: int,
    allow_remote: bool = False,
) -> str:
    host = "0.0.0.0" if allow_remote else "127.0.0.1"
    arguments: list[str | os.PathLike[str]] = [
        paths.mcp_binary,
        "mcp",
        "serve",
        "--host",
        host,
        "--mcp-port",
        str(mcp_port),
        "--port",
        str(api_port),
        "--token-file",
        paths.token_file,
        "--gpt-actions",
        "--gpt-actions-token-file",
        paths.gpt_actions_token_file,
        "--auth-token-file",
        paths.mcp_auth_token_file,
    ]
    if allow_remote:
        arguments.append("--allow-remote-mcp")
    exec_start = " ".join(systemd_quote(argument) for argument in arguments)
    return template.format(exec_start=exec_start)


def write_unit(unit_path: Path, content: bytes) -> bool:
    old_content = unit_path.read_bytes() if unit_path.is_file() else None
    if old_content == content:
        LOG.info("systemd unit is already current: %s", unit_path)
        return False
    if old_content is not None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = unit_path.with_name(f"{unit_path.name}.bak-{stamp}")
        counter = 1
        while backup.exists():
            backup = unit_path.with_name(f"{unit_path.name}.bak-{stamp}.{counter}")
            counter += 1
        atomic_write(backup, old_content, 0o600)
        LOG.info("backed up previous unit to %s", backup)
    atomic_write(unit_path, content, 0o644)
    LOG.info("installed systemd user unit: %s", unit_path)
    return True


def install_unit(
    paths: InstallPaths,
    args: argparse.Namespace,
    supervisor: Path,
    node_path: Path,
) -> bool:
    content = render_unit(
        read_project_asset(f"systemd/{SERVICE_NAME}").decode("utf-8"),
        python=resolved_path(sys.executable),
        node=node_path,
        supervisor=supervisor,
        paths=paths,
        api_port=args.api_port,
        sync_interval=args.sync_interval,
    ).encode("utf-8")
    return write_unit(paths.unit_path, content)


def install_adapter_unit(
    paths: InstallPaths,
    args: argparse.Namespace,
) -> bool:
    content = render_adapter_unit(
        read_project_asset(f"systemd/{ADAPTER_SERVICE_NAME}").decode("utf-8"),
        paths=paths,
        api_port=args.api_port,
        mcp_port=args.mcp_port,
        allow_remote=args.allow_remote_mcp,
    ).encode("utf-8")
    return write_unit(paths.adapter_unit_path, content)


def wait_for_api(port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_reason = "not attempted"
    while time.monotonic() < deadline:
        result = ping_api(port, timeout=0.75)
        last_reason = result.reason
        if result.ok:
            LOG.info("healthcheck passed: http://127.0.0.1:%d/ping", port)
            return
        time.sleep(0.25)
    raise ToolError(f"Joplin Data API healthcheck failed: {last_reason}")


def wait_for_mcp(port: int, token: str | None, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_reason = "not attempted"
    while time.monotonic() < deadline:
        result = ping_mcp(port, token, timeout=0.75)
        last_reason = result.reason
        if result.ok:
            LOG.info("healthcheck passed: http://127.0.0.1:%d/mcp", port)
            return
        time.sleep(0.25)
    raise ToolError(f"joplin-md-sync MCP healthcheck failed: {last_reason}")


def mcp_post(
    port: int,
    token: str | None,
    payload: Mapping[str, object],
    *,
    timeout: float = 5.0,
) -> tuple[int, object | None]:
    method = payload.get("method")
    method_name = method if isinstance(method, str) else "unknown"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise ToolError(f"MCP smoke request {method_name} returned HTTP {status}") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            detail = "timeout"
        elif isinstance(reason, ConnectionRefusedError):
            detail = "connection refused"
        else:
            detail = f"connection error: {type(reason).__name__}"
        raise ToolError(f"MCP smoke request {method_name} failed: {detail}") from None
    except TimeoutError:
        raise ToolError(f"MCP smoke request {method_name} failed: timeout") from None
    except OSError as exc:
        detail = "connection refused" if isinstance(exc, ConnectionRefusedError) else type(exc).__name__
        raise ToolError(f"MCP smoke request {method_name} failed: {detail}") from None
    if len(body) > 1024 * 1024:
        raise ToolError(f"MCP smoke response to {method_name} is unexpectedly large")
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError(f"MCP smoke response to {method_name} is not valid JSON") from None


def _mcp_result(payload: object, method: str) -> Mapping[str, object]:
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise ToolError(f"MCP smoke response to {method} is not a JSON-RPC response")
    if "error" in payload:
        raise ToolError(f"MCP smoke request {method} returned a JSON-RPC error")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ToolError(f"MCP smoke response to {method} has no object result")
    return result


def smoke_test_mcp_service(port: int, token: str | None, timeout: float = 5.0) -> None:
    status, initialized = mcp_post(
        port,
        token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "joplin-terminal-installer", "version": "1"},
            },
        },
        timeout=timeout,
    )
    if status != 200:
        raise ToolError(f"MCP initialize smoke request returned HTTP {status}")
    initialize_result = _mcp_result(initialized, "initialize")
    if not isinstance(initialize_result.get("serverInfo"), dict):
        raise ToolError("MCP initialize smoke response has no serverInfo")

    status, notification = mcp_post(
        port,
        token,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=timeout,
    )
    if status != 202 or notification is not None:
        raise ToolError("MCP initialized notification was not accepted")

    status, called = mcp_post(
        port,
        token,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "joplin_list_notebooks",
                "arguments": {"limit": 1},
            },
        },
        timeout=timeout,
    )
    if status != 200:
        raise ToolError(f"MCP Joplin listing smoke request returned HTTP {status}")
    tool_result = _mcp_result(called, "tools/call")
    if tool_result.get("isError") is not False:
        raise ToolError("MCP joplin_list_notebooks smoke call reported an error")
    structured = tool_result.get("structuredContent")
    if not isinstance(structured, dict):
        raise ToolError("MCP Joplin listing smoke response has no structuredContent")
    notebooks = structured.get("notebooks")
    count = structured.get("count")
    if not isinstance(notebooks, list) or type(count) is not int or count != len(notebooks):
        raise ToolError("MCP Joplin listing smoke response has an invalid object listing")
    LOG.info("MCP smoke test passed: joplin_list_notebooks returned a valid listing")


def gpt_actions_probe_status(
    port: int,
    token: str | None,
    *,
    timeout: float = 5.0,
) -> int:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/gpt/v1/tools/__installer_probe__",
        data=b"{}",
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(64 * 1024 + 1)
            return response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.read(64 * 1024 + 1)
        exc.close()
        return status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(
            f"GPT Actions smoke probe failed: {type(exc).__name__}"
        ) from None


def smoke_test_gpt_actions_service(
    port: int,
    token: str,
    *,
    timeout: float = 5.0,
) -> None:
    if gpt_actions_probe_status(port, None, timeout=timeout) != 401:
        raise ToolError("GPT Actions route accepted a request without authentication")
    if gpt_actions_probe_status(port, token, timeout=timeout) != 404:
        raise ToolError("GPT Actions bearer or route-isolation smoke check failed")
    LOG.info("GPT Actions smoke test passed: authentication and route isolation verified")


def stop_after_startup_failure(
    runner: CommandRunner,
    systemctl: Path,
    service_name: str,
) -> None:
    LOG.error("stopping %s after failed startup verification", service_name)
    try:
        result = runner.run(
            [systemctl, "--user", "stop", service_name],
            check=False,
        )
    except ToolError as exc:
        LOG.warning("could not stop %s: %s", service_name, exc)
        return
    if result.returncode != 0:
        LOG.warning("systemctl could not stop %s after startup failure", service_name)


def dry_run_plan(
    args: argparse.Namespace,
    paths: InstallPaths,
    env: Mapping[str, str],
    redactor: SecretRedactor,
) -> None:
    nextcloud_password = available_secret(args.nextcloud_password, "JOPLIN_NEXTCLOUD_PASSWORD", env)
    e2ee_password = available_secret(args.e2ee_password, "JOPLIN_E2EE_PASSWORD", env)
    redactor.add(nextcloud_password)
    redactor.add(e2ee_password)
    if args.upgrade:
        LOG.info("dry-run: would update isolated Joplin to %s", args.joplin_version)
        LOG.info(
            "dry-run: would update joplin-md-sync standalone to %s",
            args.joplin_md_sync_version,
        )
        LOG.info("dry-run: would preserve profile %s", paths.profile_dir)
        if not args.no_start_service:
            LOG.info(
                "dry-run: would restart both services and run Joplin, MCP, and GPT Actions smoke tests"
            )
        LOG.info("dry-run: managed GPT Actions token file: %s", paths.gpt_actions_token_file)
        LOG.info("dry-run: managed MCP token file: %s", paths.mcp_auth_token_file)
        return
    if args.non_interactive and not nextcloud_password:
        raise ToolError("JOPLIN_NEXTCLOUD_PASSWORD is required in non-interactive mode")
    if args.non_interactive and not args.skip_e2ee_bootstrap and not e2ee_password:
        raise ToolError("JOPLIN_E2EE_PASSWORD is required in non-interactive mode")
    LOG.info("dry-run: would check systemd user lingering before requesting passwords")
    LOG.info("dry-run: would install isolated Joplin %s", args.joplin_version)
    LOG.info("dry-run: would inspect and configure profile %s", paths.profile_dir)
    LOG.info("dry-run: would configure Nextcloud target 5 and API port %d", args.api_port)
    if not args.skip_initial_sync:
        LOG.info("dry-run: would perform initial sync")
    LOG.info("dry-run: would verify existing E2EE keys without creating a key")
    LOG.info("dry-run: would write API token to %s", paths.token_file)
    LOG.info(
        "dry-run: would install joplin-md-sync standalone %s",
        args.joplin_md_sync_version,
    )
    LOG.info("dry-run: would generate missing service tokens and preserve valid existing ones")
    LOG.info("dry-run: would require MCP bearer authentication")
    bind_host = "0.0.0.0" if args.allow_remote_mcp else "127.0.0.1"
    LOG.info(
        "dry-run: would bind the combined MCP and GPT Actions service to %s:%d",
        bind_host,
        args.mcp_port,
    )
    LOG.info("dry-run: would deploy the supervisor and two systemd units")
    if not args.no_enable_service:
        LOG.info("dry-run: would enable %s and %s", SERVICE_NAME, ADAPTER_SERVICE_NAME)
    if not args.no_start_service:
        LOG.info(
            "dry-run: would restart both services and run Joplin, MCP, and GPT Actions smoke tests"
        )
    LOG.info("dry-run: managed GPT Actions token file: %s", paths.gpt_actions_token_file)
    LOG.info("dry-run: managed MCP token file: %s", paths.mcp_auth_token_file)


def dry_run_purge(paths: InstallPaths, env: Mapping[str, str]) -> None:
    validate_purge_paths(paths, env)
    LOG.info("dry-run: would stop and disable %s and %s", ADAPTER_SERVICE_NAME, SERVICE_NAME)
    for path in (*managed_purge_paths(paths), paths.state_dir):
        LOG.info("dry-run: would remove managed path %s", path)
    LOG.info("dry-run: Nextcloud/WebDAV data and the user lingering setting would be unchanged")


class Installer:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        env: Mapping[str, str] | None = None,
        input_fn: Callable[[str], str] = terminal_input,
        getpass_fn: Callable[[str], str] = getpass.getpass,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.args = args
        self.env = dict(os.environ if env is None else env)
        self.input_fn = input_fn
        self.getpass_fn = getpass_fn
        self.redactor = SecretRedactor() if redactor is None else redactor
        self.runner = CommandRunner(self.redactor, self.env)
        self.paths = build_paths(args, self.env)

    def _restart_and_smoke(
        self,
        dependencies: Dependencies,
        mcp_auth_token: str,
        gpt_actions_token: str,
    ) -> None:
        systemctl_command(self.runner, dependencies.systemctl, "restart", SERVICE_NAME)
        try:
            if not service_active(self.runner, dependencies.systemctl, SERVICE_NAME):
                raise ToolError(f"{SERVICE_NAME} is not active after restart")
            wait_for_api(self.args.api_port)
        except ToolError:
            stop_after_startup_failure(
                self.runner,
                dependencies.systemctl,
                SERVICE_NAME,
            )
            raise

        systemctl_command(
            self.runner,
            dependencies.systemctl,
            "restart",
            ADAPTER_SERVICE_NAME,
        )
        try:
            if not service_active(self.runner, dependencies.systemctl, ADAPTER_SERVICE_NAME):
                raise ToolError(f"{ADAPTER_SERVICE_NAME} is not active after restart")
            wait_for_mcp(self.args.mcp_port, mcp_auth_token)
            smoke_test_mcp_service(self.args.mcp_port, mcp_auth_token)
            smoke_test_gpt_actions_service(
                self.args.mcp_port,
                gpt_actions_token,
            )
        except ToolError:
            stop_after_startup_failure(
                self.runner,
                dependencies.systemctl,
                ADAPTER_SERVICE_NAME,
            )
            raise

    def _upgrade(
        self,
        dependencies: Dependencies,
        mcp_auth_token: str,
        gpt_actions_token: str,
    ) -> None:
        if not self.paths.unit_path.is_file() or not self.paths.adapter_unit_path.is_file():
            raise ToolError(
                "--upgrade requires an existing Joplin and joplin-md-sync service installation"
            )
        joplin_version = (
            resolve_latest_version(self.runner, dependencies.npm)
            if self.args.joplin_version == "latest"
            else self.args.joplin_version
        )
        mcp_version = (
            resolve_latest_mcp_version()
            if self.args.joplin_md_sync_version == "latest"
            else self.args.joplin_md_sync_version
        )
        LOG.info(
            "upgrade targets: Joplin %s; joplin-md-sync %s",
            joplin_version,
            mcp_version,
        )
        if service_active(self.runner, dependencies.systemctl, ADAPTER_SERVICE_NAME):
            systemctl_command(self.runner, dependencies.systemctl, "stop", ADAPTER_SERVICE_NAME)
        if service_active(self.runner, dependencies.systemctl, SERVICE_NAME):
            systemctl_command(self.runner, dependencies.systemctl, "stop", SERVICE_NAME)
        with ProfileLock(self.paths.lock_file):
            installed_joplin = install_or_update_joplin(
                self.runner,
                dependencies,
                self.paths,
                joplin_version,
            )
            LOG.info("verified Joplin %s", installed_joplin)
            installed_mcp = install_or_update_mcp(
                self.runner,
                self.paths,
                mcp_version,
            )
            LOG.info("verified joplin-md-sync standalone %s", installed_mcp)
            validate_distinct_service_tokens(
                self.paths,
                gpt_actions_token,
                mcp_auth_token,
            )
            configure_mcp_auth_token(self.paths, mcp_auth_token)
            configure_gpt_actions_token(self.paths, gpt_actions_token)
            install_adapter_unit(self.paths, self.args)
        systemctl_command(self.runner, dependencies.systemctl, "daemon-reload")
        if self.args.no_start_service:
            LOG.info("services remain stopped due to --no-start-service")
            return
        self._restart_and_smoke(dependencies, mcp_auth_token, gpt_actions_token)
        LOG.info("Joplin Terminal and joplin-md-sync service upgrade completed")

    def run(self) -> None:
        validate_args(self.args)
        if self.args.purge:
            LOG.info("Joplin profile selected for purge: %s", self.paths.profile_dir)
            if self.args.dry_run:
                dry_run_purge(self.paths, self.env)
                return
            validate_purge_paths(self.paths, self.env)
            confirm_purge(self.args, self.paths, input_fn=self.input_fn)
            purge_installation(
                self.runner,
                purge_systemctl_path(),
                self.paths,
                self.env,
            )
            return
        dependencies = check_dependencies(self.runner)
        LOG.info("isolated npm prefix: %s", self.paths.npm_prefix)
        LOG.info("Joplin profile: %s", self.paths.profile_dir)
        LOG.info("Joplin Data API port: %d", self.args.api_port)
        mcp_host = "0.0.0.0" if self.args.allow_remote_mcp else "127.0.0.1"
        LOG.info(
            "joplin-md-sync listener: http://%s:%d (MCP /mcp; GPT Actions /api/gpt/v1/*)",
            mcp_host,
            self.args.mcp_port,
        )
        if self.args.dry_run:
            dry_run_plan(self.args, self.paths, self.env, self.redactor)
            return
        for name in ("JOPLIN_GPT_ACTIONS_TOKEN", "JOPLIN_MCP_AUTH_TOKEN"):
            if self.env.get(name):
                LOG.warning("%s is ignored; the installer manages service tokens", name)
        mcp_auth_token, gpt_actions_token = resolve_service_tokens(
            self.paths,
            self.redactor,
        )
        if self.args.upgrade:
            self._upgrade(dependencies, mcp_auth_token, gpt_actions_token)
            report_service_token_paths(self.paths)
            del mcp_auth_token
            del gpt_actions_token
            return

        configure_lingering(
            self.runner,
            dependencies.loginctl,
            self.args,
            input_fn=self.input_fn,
        )
        terminal_was_active = service_active(
            self.runner,
            dependencies.systemctl,
            SERVICE_NAME,
        )
        adapter_was_active = service_active(
            self.runner,
            dependencies.systemctl,
            ADAPTER_SERVICE_NAME,
        )
        if adapter_was_active:
            LOG.info("stopping active %s", ADAPTER_SERVICE_NAME)
            systemctl_command(
                self.runner,
                dependencies.systemctl,
                "stop",
                ADAPTER_SERVICE_NAME,
            )
        if terminal_was_active:
            LOG.info("stopping active %s for exclusive profile access", SERVICE_NAME)
            systemctl_command(self.runner, dependencies.systemctl, "stop", SERVICE_NAME)

        units_changed = False
        with ProfileLock(self.paths.lock_file):
            version = install_or_update_joplin(
                self.runner,
                dependencies,
                self.paths,
                self.args.joplin_version,
            )
            LOG.info("verified Joplin %s", version)
            nextcloud_password = resolve_secret(
                self.args.nextcloud_password,
                "JOPLIN_NEXTCLOUD_PASSWORD",
                "Nextcloud password: ",
                env=self.env,
                non_interactive=self.args.non_interactive,
                getpass_fn=self.getpass_fn,
            )
            self.redactor.add(nextcloud_password)
            configure_profile(
                self.runner,
                self.paths,
                self.args,
                nextcloud_password,
                input_fn=self.input_fn,
            )
            del nextcloud_password
            if not self.args.skip_initial_sync:
                run_sync(self.runner, self.paths, "performing initial Nextcloud sync")
            bootstrap_e2ee(
                self.runner,
                self.paths,
                self.args,
                self.redactor,
                self.env,
                getpass_fn=self.getpass_fn,
            )
            run_sync(self.runner, self.paths, "performing post-decryption sync")
            extract_api_token(self.runner, self.paths, self.redactor)
            mcp_installed_version = install_or_update_mcp(
                self.runner,
                self.paths,
                self.args.joplin_md_sync_version,
            )
            LOG.info(
                "verified joplin-md-sync standalone %s",
                mcp_installed_version,
            )
            validate_distinct_service_tokens(
                self.paths,
                gpt_actions_token,
                mcp_auth_token,
            )
            configure_mcp_auth_token(self.paths, mcp_auth_token)
            configure_gpt_actions_token(self.paths, gpt_actions_token)
            supervisor, _common = deploy_supervisor(self.paths)
            joplin_unit_changed = install_unit(
                self.paths,
                self.args,
                supervisor,
                dependencies.node,
            )
            adapter_unit_changed = install_adapter_unit(self.paths, self.args)
            units_changed = joplin_unit_changed or adapter_unit_changed

        # Reload even when content is unchanged: the user manager may have restarted.
        systemctl_command(self.runner, dependencies.systemctl, "daemon-reload")
        if units_changed:
            LOG.info("systemd service definitions changed")
        if not self.args.no_enable_service:
            systemctl_command(self.runner, dependencies.systemctl, "enable", SERVICE_NAME)
            systemctl_command(
                self.runner,
                dependencies.systemctl,
                "enable",
                ADAPTER_SERVICE_NAME,
            )
        if self.args.no_start_service:
            LOG.info("services were not started due to --no-start-service")
        else:
            self._restart_and_smoke(
                dependencies,
                mcp_auth_token,
                gpt_actions_token,
            )
        LOG.info("Joplin Terminal and joplin-md-sync service installation completed")
        report_service_token_paths(self.paths)
        del mcp_auth_token
        del gpt_actions_token


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    env = dict(os.environ)
    redactor = SecretRedactor()
    try:
        args = build_parser(env).parse_args(argv)
        redactor.add(args.nextcloud_password)
        redactor.add(args.e2ee_password)
        redactor.add(env.get("JOPLIN_NEXTCLOUD_PASSWORD"))
        redactor.add(env.get("JOPLIN_E2EE_PASSWORD"))
        redactor.add(env.get("JOPLIN_MCP_AUTH_TOKEN"))
        redactor.add(env.get("JOPLIN_GPT_ACTIONS_TOKEN"))
        configure_logging(args.verbose, redactor)
        Installer(args, env=env, redactor=redactor).run()
        return 0
    except ToolError as exc:
        if not logging.getLogger().handlers:
            configure_logging(False, redactor)
        LOG.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.error("installation interrupted")
        return 130
    except Exception as exc:
        if not logging.getLogger().handlers:
            configure_logging(False, redactor)
        verbose = "args" in locals() and bool(args.verbose)
        if verbose:
            LOG.exception("unexpected installer failure")
        else:
            LOG.error("unexpected installer failure: %s", type(exc).__name__)
        return 1
    finally:
        redactor.clear()


if __name__ == "__main__":
    raise SystemExit(main())
