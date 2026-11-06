"""Joplin Terminal installation: releases."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import cast

from .cli import _VERSION_RE
from .commands import CommandRunner
from .common import (
    ToolError,
)
from .models import Dependencies, InstallPaths

LOG = logging.getLogger("joplin-terminal-installer")


NPM_TIMEOUT = 900.0


DOWNLOAD_TIMEOUT = 120.0


MAX_RELEASE_FILE_BYTES = 128 * 1024 * 1024


MCP_REPOSITORY = "kogeler/joplin-md-sync"


MCP_RELEASES_URL = os.environ.get(
    "JOPLIN_MD_SYNC_RELEASES_URL",
    f"https://github.com/{MCP_REPOSITORY}/releases",
).rstrip("/")


MCP_RELEASE_API_URL = os.environ.get(
    "JOPLIN_MD_SYNC_RELEASE_API_URL",
    f"https://api.github.com/repos/{MCP_REPOSITORY}/releases/latest",
)


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
    return cast(bytes, data)


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
        raise ToolError(f"refusing non-working joplin-md-sync executable: {binary}{suffix}")
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
