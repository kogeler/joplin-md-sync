"""Joplin Terminal installer: operations."""

from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .cli import (
    selected_sync_target,
)
from .common import (
    ADAPTER_SERVICE_NAME,
    SERVICE_NAME,
)
from .managed import (
    managed_purge_paths,
    owned_mcp_binary,
    retired_config_paths,
    validate_purge_paths,
)
from .models import InstallPaths
from .service import adapter_unit_enables_actions

LOG = logging.getLogger("joplin-terminal-installer")


def dry_run_plan(
    args: argparse.Namespace,
    paths: InstallPaths,
) -> None:
    if getattr(args, "github_oauth", False):
        LOG.info(
            "dry-run: would validate GitHub OAuth CLI values before service changes",
        )
        LOG.info(
            "dry-run: would enable GitHub OAuth on the existing MCP port with scoped state writes"
        )
    elif getattr(args, "no_github_oauth", False):
        LOG.info("dry-run: would disable OAuth routes and preserve configuration and grants")
    if args.upgrade:
        LOG.info("dry-run: would update isolated Joplin to %s", args.joplin_version)
        LOG.info(
            "dry-run: would update joplin-md-sync standalone to %s",
            args.joplin_md_sync_version,
        )
        LOG.info("dry-run: would preserve profile %s", paths.profile_dir)
        if not args.no_start_service:
            LOG.info("dry-run: would restart both services and run Joplin and MCP smoke tests")
        _dry_run_token_files(args, paths)
        return
    if not args.sync_target:
        LOG.info(
            "dry-run: interactive setup would request missing configuration and optional features"
        )
        return
    target = selected_sync_target(args)
    LOG.info("dry-run: would check systemd user lingering before requesting passwords")
    LOG.info("dry-run: would install isolated Joplin %s", args.joplin_version)
    LOG.info("dry-run: would inspect and configure profile %s", paths.profile_dir)
    LOG.info(
        "dry-run: would configure %s target %d and API port %d",
        target.label,
        target.target_id,
        args.api_port,
    )
    if args.sync_secret_file:
        LOG.info("dry-run: would read the sync credential from %s", args.sync_secret_file)
    if target.browser_auth or target.saml_auth:
        LOG.info("dry-run: would complete interactive %s authentication", target.label)
    LOG.info("dry-run: would perform and verify the initial sync")
    LOG.info("dry-run: would unlock existing E2EE keys only when the remote uses E2EE")
    if args.e2ee_password_file:
        LOG.info("dry-run: would read the E2EE password from %s", args.e2ee_password_file)
    LOG.info("dry-run: would write API token to %s", paths.token_file)
    LOG.info(
        "dry-run: would install joplin-md-sync standalone %s",
        args.joplin_md_sync_version,
    )
    LOG.info("dry-run: would generate a missing MCP bearer token and preserve a valid one")
    LOG.info("dry-run: would require MCP bearer authentication")
    bind_host = "0.0.0.0" if args.allow_remote_mcp else "127.0.0.1"
    LOG.info(
        "dry-run: would bind the MCP service to %s:%d",
        bind_host,
        args.mcp_port,
    )
    LOG.info("dry-run: would deploy the supervisor and two systemd units")
    if not args.no_enable_service:
        LOG.info("dry-run: would enable %s and %s", SERVICE_NAME, ADAPTER_SERVICE_NAME)
    if not args.no_start_service:
        LOG.info("dry-run: would restart both services and run Joplin and MCP smoke tests")
    _dry_run_token_files(args, paths)


def _dry_run_token_files(args: argparse.Namespace, paths: InstallPaths) -> None:
    LOG.info("dry-run: managed MCP token file: %s", paths.mcp_auth_token_file)
    if adapter_unit_enables_actions(paths):
        LOG.info(
            "dry-run: would remove ChatGPT Actions from the installed service, "
            "which requires joplin-md-sync 2.0.0 or newer"
        )
    for path in retired_config_paths(paths):
        if path.is_symlink() or path.is_file():
            if args.no_start_service:
                LOG.info("dry-run: would keep retired file %s; services are not started", path)
            else:
                LOG.info(
                    "dry-run: would remove retired file %s after the services pass their checks",
                    path,
                )


def dry_run_purge(paths: InstallPaths, env: Mapping[str, str]) -> None:
    validate_purge_paths(paths, env)
    LOG.info("dry-run: would stop and disable %s and %s", ADAPTER_SERVICE_NAME, SERVICE_NAME)
    retired = retired_config_paths(paths)
    for path in (*managed_purge_paths(paths), paths.state_dir):
        if path in retired and not (path.exists() or path.is_symlink()):
            continue
        if path == paths.mcp_binary and not owned_mcp_binary(paths):
            if path.exists() or path.is_symlink():
                LOG.info("dry-run: would preserve unrecorded executable %s", path)
            continue
        LOG.info("dry-run: would remove managed path %s", path)
    LOG.info("dry-run: remote sync data and the user lingering setting would be unchanged")


def backup_upgrade_files(paths: InstallPaths) -> Path:
    """Retain executable/service files, not the database or sync credentials."""
    paths.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="upgrade-backup-", dir=paths.state_dir))
    for source in (paths.mcp_binary, paths.unit_path, paths.adapter_unit_path, paths.deploy_dir):
        target = backup / source.name
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.is_file() or source.is_symlink():
            shutil.copy2(source, target, follow_symlinks=False)
    LOG.info("previous executable and service files retained in %s", backup)
    return backup
