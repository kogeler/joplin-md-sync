"""Joplin Terminal installation: service."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .assets import read_project_asset
from .cli import github_oauth_arguments
from .commands import CommandRunner
from .common import (
    ADAPTER_SERVICE_NAME,
    SERVICE_NAME,
    ToolError,
    absolute_path,
    atomic_write,
    lock_path_for_profile,
    ping_api,
    ping_mcp,
    resolved_path,
    systemd_quote,
)
from .models import GITHUB_OAUTH_FIELDS, InstallPaths

LOG = logging.getLogger("joplin-terminal-installer")


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


def deploy_supervisor(paths: InstallPaths) -> tuple[Path, Path]:
    paths.deploy_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.deploy_dir.chmod(0o700)
    runner_target = paths.deploy_dir / "run_joplin_terminal.py"
    common_target = paths.deploy_dir / "joplin_terminal_common.py"
    atomic_write(runner_target, read_project_asset("run_joplin_terminal.py"), 0o700)
    atomic_write(common_target, read_project_asset("joplin_terminal_installer/common.py"), 0o600)
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
    extra_write_paths: Sequence[Path] = (),
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
        systemd_quote(path)
        for path in (paths.profile_dir, paths.lock_file.parent, *extra_write_paths)
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
    github_oauth_args: Sequence[str] = (),
    oauth_state_path: Path | None = None,
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
    if github_oauth_args:
        if oauth_state_path is None:
            raise ToolError("OAuth unit requires the validated state directory")
        arguments.extend(github_oauth_args)
    exec_start = " ".join(systemd_quote(argument) for argument in arguments)
    content = template.format(exec_start=exec_start)
    if github_oauth_args:
        content = content.replace("TimeoutStopSec=30s", "TimeoutStopSec=70s")
        content = content.replace(
            "ProtectSystem=strict\n",
            "ProtectSystem=strict\nReadWritePaths=" + systemd_quote(oauth_state_path) + "\n",
        )
    return content


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
    extra_write_paths = getattr(args, "extra_write_paths", ()) or (
        (resolved_path(args.sync_location),)
        if args.sync_target == "filesystem" and args.sync_location
        else ()
    )
    content = render_unit(
        read_project_asset(f"systemd/{SERVICE_NAME}").decode("utf-8"),
        python=resolved_path(sys.executable),
        node=node_path,
        supervisor=supervisor,
        paths=paths,
        api_port=args.api_port,
        sync_interval=args.sync_interval,
        extra_write_paths=extra_write_paths,
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
        github_oauth_args=github_oauth_arguments(args),
        oauth_state_path=getattr(args, "github_oauth_state_dir", None),
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
        detail = (
            "connection refused" if isinstance(exc, ConnectionRefusedError) else type(exc).__name__
        )
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
            return cast(int, response.status)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.read(64 * 1024 + 1)
        exc.close()
        return status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"GPT Actions smoke probe failed: {type(exc).__name__}") from None


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


def installed_service_options(paths: InstallPaths) -> dict[str, object]:
    """Read only our systemd units, never Joplin's profile or credential values."""

    def unit_lines(path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise ToolError(f"cannot read installed unit {path}") from None

    def directive(path: Path, name: str) -> list[str]:
        lines = [line[len(name) + 1 :] for line in unit_lines(path) if line.startswith(name + "=")]
        if len(lines) != 1:
            raise ToolError(f"cannot recover {name} from installed unit {path}")
        try:
            return [value.replace("%%", "%") for value in shlex.split(lines[0])]
        except ValueError:
            raise ToolError(f"invalid installed unit {path}") from None

    backend = directive(paths.unit_path, "ExecStart")
    adapter = directive(paths.adapter_unit_path, "ExecStart")

    def value(command: list[str], option: str) -> str:
        try:
            if command.count(option) != 1:
                raise ValueError
            return command[command.index(option) + 1]
        except (IndexError, ValueError):
            raise ToolError(f"installed units lack an unambiguous {option}") from None

    host = value(adapter, "--host")
    if host not in ("127.0.0.1", "0.0.0.0") or (
        (host == "0.0.0.0") != ("--allow-remote-mcp" in adapter)
    ):
        raise ToolError("installed adapter has an unsupported listener configuration")
    try:
        api_port = int(value(backend, "--api-port"))
        if api_port != int(value(adapter, "--port")):
            raise ToolError("installed service API ports disagree")
        options: dict[str, object] = {
            "api_port": api_port,
            "mcp_port": int(value(adapter, "--mcp-port")),
            "sync_interval": int(value(backend, "--sync-interval")),
            "profile_dir": value(backend, "--profile-dir"),
            "joplin_prefix": str(Path(value(backend, "--joplin-path")).parent.parent),
            "allow_remote_mcp": host == "0.0.0.0",
            "github_oauth": "--github-oauth" in adapter,
        }
    except ValueError:
        raise ToolError("installed units contain invalid port or interval values") from None
    prefix = resolved_path(str(options["joplin_prefix"]))
    expected = (
        (adapter[0], prefix / "bin" / "joplin-md-sync"),
        (value(adapter, "--token-file"), paths.token_file),
        (value(adapter, "--auth-token-file"), paths.mcp_auth_token_file),
        (value(adapter, "--gpt-actions-token-file"), paths.gpt_actions_token_file),
        (
            value(backend, "--lock-file"),
            lock_path_for_profile(resolved_path(str(options["profile_dir"])), paths.state_dir),
        ),
    )
    if any(resolved_path(actual) != resolved_path(wanted) for actual, wanted in expected):
        raise ToolError(
            "installed service paths disagree with the managed layout; "
            "keep the original XDG configuration/state environment and restore consistent units"
        )
    if "--github-oauth" in adapter:
        for field in GITHUB_OAUTH_FIELDS:
            flag = "--github-oauth-" + field.replace("_", "-")
            if flag in adapter:
                raw = value(adapter, flag)
                try:
                    parsed: object = int(raw) if field == "access_token_ttl" else raw
                except ValueError:
                    raise ToolError(f"installed adapter has an invalid {flag} value") from None
                options["github_oauth_" + field] = parsed
    # Preserve filesystem sync sandbox exceptions when regenerating the backend.
    if any(line.startswith("ReadWritePaths=") for line in unit_lines(paths.unit_path)):
        protected = {str(options["profile_dir"]), str(Path(value(backend, "--lock-file")).parent)}
        options["extra_write_paths"] = tuple(
            Path(path)
            for path in directive(paths.unit_path, "ReadWritePaths")
            if path not in protected
        )
    else:
        # Older snap units have no mount namespace. Recover a filesystem
        # target through the public CLI before enabling the current sandbox.
        options["recover_filesystem_target"] = True
    return options
