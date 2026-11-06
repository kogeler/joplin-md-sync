"""Joplin Terminal installation: managed."""

from __future__ import annotations

import argparse
import json
import logging
import os
import pwd
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from .cli import terminal_input
from .commands import CommandRunner
from .common import (
    ADAPTER_SERVICE_NAME,
    SERVICE_NAME,
    ProfileLock,
    ToolError,
    absolute_path,
    resolved_path,
    safe_command,
)
from .credentials import load_secret_file
from .models import InstallPaths
from .releases import _safe_launcher_state

LOG = logging.getLogger("joplin-terminal-installer")


def purge_systemctl_path() -> Path:
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise ToolError("missing required program: systemctl")
    return absolute_path(systemctl)


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
        "OAuth state": paths.oauth_state_path,
        "supervisor": paths.deploy_dir,
    }
    filesystem_root = Path(paths.profile_dir.anchor or "/")
    for label, path in directories.items():
        if path in (filesystem_root, home) or len(path.parts) < 3:
            raise ToolError(f"refusing to purge unsafe {label} path: {path}")
    for label, path in directories.items():
        if label != "state" and paths.state_dir.is_relative_to(path):
            raise ToolError(f"refusing to purge {label} because it contains the profile lock state")


# Installer-owned entries of the shared joplin-md-sync config directory. Other
# files there, such as operator-created OAuth secrets, are not managed.
MANAGED_CONFIG_ENTRIES = (
    "installer-settings.json",
    "oauth-settings.json",
    "oauth-managed-state.json",
    "oauth-secrets",
)


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
        *managed_oauth_state_paths(paths),
        paths.mcp_auth_token_file,
        paths.gpt_actions_token_file,
        *(paths.mcp_config_dir / name for name in MANAGED_CONFIG_ENTRIES),
        paths.deploy_dir,
    )


def managed_oauth_state_paths(paths: InstallPaths) -> tuple[Path, ...]:
    marker = paths.mcp_config_dir / "oauth-managed-state.json"
    if not marker.exists():
        return ()
    try:
        value = json.loads(load_secret_file(marker, "managed OAuth state marker"))
        if value != {"schema": 1, "path": str(paths.oauth_state_path)}:
            raise ValueError
    except (ValueError, TypeError):
        raise ToolError(
            "managed OAuth state marker is invalid; no arbitrary paths will be purged"
        ) from None
    return (paths.oauth_state_path,)


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
    for parent in {paths.npm_prefix.parent, paths.profile_dir.parent, paths.mcp_config_dir}:
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
    explicit_linger = vars(args).get("explicit_options", set())
    if not enable and not args.non_interactive and "enable_linger" not in explicit_linger:
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
    command: list[str | os.PathLike[str]] = [loginctl, "enable-linger", username]
    LOG.info("enabling systemd user lingering: %s", safe_command(command))
    runner.run(command)
    if not lingering_enabled(runner, loginctl, username):
        raise ToolError(f"systemd user lingering remains disabled for user {username}")
    LOG.info("systemd user lingering is enabled for %s", username)
    return True
