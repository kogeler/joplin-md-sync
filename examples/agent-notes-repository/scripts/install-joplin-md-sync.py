#!/usr/bin/env python3
"""Install the verified latest joplin-md-sync standalone into this repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

REPOSITORY = "kogeler/joplin-md-sync"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REQUIRED_COMMANDS = {"init", "pull", "push", "diff", "mcp serve"}


class InstallError(RuntimeError):
    """An expected installation failure with a user-safe message."""


class Release(NamedTuple):
    version: str
    tag: str
    asset_url: str
    checksums_url: str


def standalone_asset(system: str | None = None, machine: str | None = None) -> str:
    current_system = sys.platform if system is None else system
    current_machine = platform.machine().lower() if machine is None else machine.lower()
    architectures = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    architecture = architectures.get(current_machine)
    if current_system == "linux" and architecture is not None:
        return f"joplin-md-sync-linux-{architecture}"
    if current_system == "win32" and architecture == "amd64":
        return "joplin-md-sync-windows-amd64.exe"
    raise InstallError(
        "no standalone release is available for "
        f"{current_system}/{current_machine or 'unknown'}"
    )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "joplin-md-sync-agent-repository-installer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def fetch_bytes(url: str, *, limit: int) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=DOWNLOAD_TIMEOUT) as response:
            advertised = response.headers.get("Content-Length")
            if advertised is not None and int(advertised) > limit:
                raise InstallError(f"download from {url} is unexpectedly large")
            data = response.read(limit + 1)
    except InstallError:
        raise
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        raise InstallError(f"could not download {url}: {exc}") from None
    if len(data) > limit:
        raise InstallError(f"download from {url} is unexpectedly large")
    return data


def parse_release(payload: object, asset_name: str) -> Release:
    if not isinstance(payload, dict):
        raise InstallError("GitHub release metadata is not a JSON object")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise InstallError("GitHub did not return a stable published release")

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise InstallError("GitHub release has no valid vX.Y.Z tag")
    version = tag[1:]
    if VERSION_RE.fullmatch(version) is None:
        raise InstallError("GitHub release has no valid vX.Y.Z tag")

    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise InstallError("GitHub release has no asset inventory")
    asset_urls: dict[str, str] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if name in asset_urls:
            raise InstallError(f"GitHub release contains duplicate asset {name}")
        expected_url = f"{REPOSITORY_URL}/releases/download/{tag}/{name}"
        if url != expected_url:
            raise InstallError(f"GitHub release contains an unexpected URL for {name}")
        asset_urls[name] = url

    missing = [name for name in (asset_name, "SHA256SUMS.txt") if name not in asset_urls]
    if missing:
        raise InstallError(f"GitHub release is missing required asset(s): {', '.join(missing)}")
    return Release(version, tag, asset_urls[asset_name], asset_urls["SHA256SUMS.txt"])


def resolve_release(asset_name: str, requested_version: str | None = None) -> Release:
    if requested_version is None:
        url = f"{RELEASE_API_URL}/latest"
    else:
        version = requested_version.removeprefix("v")
        if VERSION_RE.fullmatch(version) is None:
            raise InstallError("--release must be a stable X.Y.Z version")
        url = f"{RELEASE_API_URL}/tags/v{version}"
    try:
        payload = json.loads(fetch_bytes(url, limit=MAX_METADATA_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InstallError("GitHub returned invalid release JSON") from None
    release = parse_release(payload, asset_name)
    if requested_version is not None and release.version != requested_version.removeprefix("v"):
        raise InstallError("GitHub returned a different release than requested")
    return release


def parse_checksum(contents: bytes, asset_name: str) -> str:
    try:
        lines = contents.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise InstallError("SHA256SUMS.txt is not ASCII") from None

    matches: list[str] = []
    for line in lines:
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or fields[1].lstrip("*") != asset_name:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]) is None:
            raise InstallError(f"SHA256SUMS.txt has an invalid checksum for {asset_name}")
        matches.append(fields[0].lower())
    if len(matches) != 1:
        raise InstallError(
            f"SHA256SUMS.txt must contain exactly one checksum for {asset_name}"
        )
    return matches[0]


def download_asset(url: str, target: Path, expected_digest: str) -> None:
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(_request(url), timeout=DOWNLOAD_TIMEOUT) as response:
            advertised = response.headers.get("Content-Length")
            if advertised is not None and int(advertised) > MAX_ASSET_BYTES:
                raise InstallError("release executable is unexpectedly large")
            with target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise InstallError("release executable is unexpectedly large")
                    digest.update(chunk)
                    output.write(chunk)
    except InstallError:
        raise
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        raise InstallError(f"could not download release executable: {exc}") from None
    if total == 0:
        raise InstallError("release executable download was empty")
    if digest.hexdigest() != expected_digest:
        raise InstallError("release executable SHA-256 does not match SHA256SUMS.txt")


def _run_json(binary: Path, command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(
            [str(binary), *command, "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"could not run downloaded executable: {exc}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        suffix = f": {detail}" if detail else ""
        raise InstallError(f"downloaded executable failed {' '.join(command)}{suffix}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise InstallError("downloaded executable returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise InstallError("downloaded executable returned an unexpected JSON value")
    return payload


def verify_binary(binary: Path, expected_version: str | None = None) -> str:
    version_payload = _run_json(binary, ["version"])
    version = version_payload.get("tool_version")
    if (
        version_payload.get("exit_code") != 0
        or version_payload.get("repository") != REPOSITORY_URL
        or version_payload.get("distribution") != "standalone"
        or not isinstance(version, str)
        or VERSION_RE.fullmatch(version) is None
    ):
        raise InstallError("executable is not a recognized joplin-md-sync standalone")
    if expected_version is not None and version != expected_version:
        raise InstallError(
            f"executable reports version {version}, expected release {expected_version}"
        )

    capabilities = _run_json(binary, ["capabilities"])
    commands = capabilities.get("commands")
    if (
        not isinstance(commands, list)
        or any(not isinstance(command, str) for command in commands)
        or not REQUIRED_COMMANDS.issubset(set(commands))
    ):
        raise InstallError("standalone is missing required file-sync or MCP commands")
    return version


def install(requested_version: str | None = None) -> dict[str, object]:
    asset_name = standalone_asset()
    release = resolve_release(asset_name, requested_version)
    repository_root = Path(__file__).resolve().parents[1]
    tools_dir = repository_root / ".tools"
    output_name = "joplin-md-sync.exe" if sys.platform == "win32" else "joplin-md-sync"
    output = tools_dir / output_name

    if tools_dir.is_symlink() or (tools_dir.exists() and not tools_dir.is_dir()):
        raise InstallError("refusing unsafe .tools path")
    tools_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

    previous_version: str | None = None
    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_file():
            raise InstallError(f"refusing to overwrite unsafe path {output}")
        try:
            previous_version = verify_binary(output)
        except InstallError as exc:
            raise InstallError(f"refusing to overwrite unrecognized file {output}: {exc}") from None
        if previous_version == release.version:
            return {
                "installed": False,
                "path": str(output),
                "version": release.version,
                "reason": "already current",
            }

    checksum = parse_checksum(
        fetch_bytes(release.checksums_url, limit=MAX_METADATA_BYTES), asset_name
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".joplin-md-sync.",
        suffix=".exe" if sys.platform == "win32" else "",
        dir=tools_dir,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        download_asset(release.asset_url, temporary, checksum)
        temporary.chmod(0o755)
        verify_binary(temporary, release.version)
        os.replace(temporary, output)
        output.chmod(0o755)
        verify_binary(output, release.version)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    return {
        "installed": True,
        "path": str(output),
        "previous_version": previous_version,
        "version": release.version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        metavar="X.Y.Z",
        help="install one stable release instead of the latest stable release",
    )
    args = parser.parse_args()
    try:
        result = install(args.release)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
