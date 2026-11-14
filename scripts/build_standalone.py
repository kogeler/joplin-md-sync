#!/usr/bin/env python3
"""Build the current platform's one-file joplin-md-sync executable."""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def oauth_pyinstaller_options() -> list[str]:
    """Metadata and dynamic backends required by the embedded OAuth runtime."""
    return [
        "--recursive-copy-metadata",
        "fastmcp",
        "--recursive-copy-metadata",
        "aiofile",
        "--collect-data",
        "fastmcp",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "caio",
    ]


# CI pull request candidates stamp a prerelease of .version, e.g. 1.8.0-pr48.g7bd6520.
CANDIDATE_VERSION_ENV = "JOPLIN_MD_SYNC_BUILD_VERSION"


def version_resource(
    environ: dict[str, str] | None = None, directory: Path = REPO / "build" / "candidate-version"
) -> Path:
    """Return the version file to bundle; a candidate keeps .version as its base."""
    candidate = (os.environ if environ is None else environ).get(CANDIDATE_VERSION_ENV)
    if not candidate:
        return REPO / ".version"
    release = (REPO / ".version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(re.escape(release) + r"-pr[1-9][0-9]*\.g[0-9a-f]{7,40}", candidate):
        raise ValueError(f"{CANDIDATE_VERSION_ENV} must be {release}-prN.gSHA, got {candidate!r}")
    stamped = directory / ".version"
    directory.mkdir(parents=True, exist_ok=True)
    stamped.write_text(candidate + "\n", encoding="utf-8")
    return stamped


def standalone_architecture(machine: str = platform.machine()) -> str:
    architectures = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    architecture = architectures.get(machine.casefold())
    if architecture is None:
        raise ValueError(f"unsupported standalone architecture: {machine}")
    return architecture


def standalone_name(system: str = sys.platform, machine: str = platform.machine()) -> str:
    architecture = standalone_architecture(machine)
    if system == "linux":
        return f"joplin-md-sync-linux-{architecture}"
    if system == "win32" and architecture == "amd64":
        return f"joplin-md-sync-windows-{architecture}.exe"
    raise ValueError(f"unsupported standalone target: {system}/{architecture}")


def build() -> Path:
    import PyInstaller.__main__

    artifact_name = standalone_name()
    executable_name = artifact_name.removesuffix(".exe")
    dist = REPO / "dist"
    work = REPO / "build" / "pyinstaller"
    spec = work / "spec"
    dist.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)

    PyInstaller.__main__.run(
        [
            str(REPO / "scripts" / "standalone_entry.py"),
            "--name",
            executable_name,
            "--onefile",
            "--console",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            "--specpath",
            str(spec),
            "--paths",
            str(REPO / "src"),
            "--add-data",
            f"{version_resource()}{os.pathsep}joplin_md_sync",
            *oauth_pyinstaller_options(),
        ]
    )

    artifact = dist / artifact_name
    if not artifact.is_file():
        raise RuntimeError(f"PyInstaller did not create {artifact}")
    print(artifact)
    return artifact


if __name__ == "__main__":
    build()
