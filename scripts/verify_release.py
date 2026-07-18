#!/usr/bin/env python3
"""Release consistency checks (used by CI and the release workflow).

Verifies:
* the root .version file is the single version source (pyproject reads it
  dynamically) and agent-manifest.json matches it;
* an optional --tag matches the package version;
* built artifacts in dist/ (when present) carry the right version;
* no token-like fixture secrets leak into the artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from joplin_md_sync import (  # noqa: E402
    OUTPUT_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    STATE_SCHEMA_VERSION,
    __version__,
)

FORBIDDEN_PATTERNS = [b"test-token-0123456789abcdef", b"JOPLIN_TOKEN="]
STANDALONE_NAMES = {
    "joplin-md-sync-linux-amd64",
    "joplin-md-sync-linux-arm64",
    "joplin-md-sync-windows-amd64.exe",
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def check_version_file() -> None:
    version_file = (REPO / ".version").read_text(encoding="utf-8").strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version_file):
        fail(f".version does not contain a plain semver string: {version_file!r}")
    if version_file != __version__:
        fail(f".version {version_file} != resolved package version {__version__}")
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    if 'dynamic = ["version"]' not in pyproject or '{ file = ".version" }' not in pyproject:
        fail("pyproject.toml no longer reads the version from .version")
    print(f"ok .version {version_file} (single source, pyproject reads it dynamically)")


def check_manifest() -> None:
    manifest = json.loads((REPO / "agent-manifest.json").read_text(encoding="utf-8"))
    expectations = {
        "version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "name": "joplin-md-sync",
        "cli": "joplin-md-sync",
    }
    for key, expected in expectations.items():
        if manifest.get(key) != expected:
            fail(f"agent-manifest.json {key}={manifest.get(key)!r}, expected {expected!r}")
    print("ok agent-manifest.json matches package metadata")


def check_tag(tag: str) -> None:
    if tag.lstrip("v") != __version__:
        fail(f"git tag {tag} does not match package version {__version__}")
    print(f"ok tag {tag} matches version")


def check_checksums(artifacts: list[Path], checksum_path: Path) -> None:
    if not checksum_path.is_file():
        fail("dist/SHA256SUMS.txt is missing")
    expected: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            fail(f"malformed checksum line: {line!r}")
        expected[name] = digest
    actual_names = {artifact.name for artifact in artifacts}
    if set(expected) != actual_names:
        fail(f"checksum inventory {sorted(expected)} != artifacts {sorted(actual_names)}")
    for artifact in artifacts:
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if expected[artifact.name] != actual:
            fail(f"checksum mismatch for {artifact.name}")


def check_standalones(artifacts: list[Path], *, require_all: bool) -> None:
    package_artifacts = {
        artifact
        for artifact in artifacts
        if artifact.suffix in (".whl", ".pyz") or artifact.name.endswith(".tar.gz")
    }
    standalone_names = {artifact.name for artifact in artifacts if artifact not in package_artifacts}
    unexpected = standalone_names - STANDALONE_NAMES
    if unexpected:
        fail(f"unexpected release artifacts: {sorted(unexpected)}")
    if not standalone_names:
        fail("dist/ does not contain a standalone executable")
    if require_all and standalone_names != STANDALONE_NAMES:
        fail(
            f"standalone inventory {sorted(standalone_names)} != "
            f"{sorted(STANDALONE_NAMES)}"
        )


def check_artifacts(*, require_all_standalones: bool = False) -> None:
    dist = REPO / "dist"
    if not dist.is_dir():
        print("skip artifact checks (no dist/)")
        return
    artifacts = sorted(
        artifact
        for artifact in dist.iterdir()
        if artifact.is_file() and artifact.name != "SHA256SUMS.txt"
    )
    wheel = [p for p in artifacts if p.suffix == ".whl"]
    if wheel and __version__ not in wheel[0].name:
        fail(f"wheel {wheel[0].name} does not carry version {__version__}")
    check_standalones(artifacts, require_all=require_all_standalones)
    for artifact in artifacts:
        data = artifact.read_bytes()
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in data:
                fail(f"secret-like pattern {pattern!r} found in {artifact.name}")
        if artifact.suffix in (".whl", ".pyz"):
            with zipfile.ZipFile(artifact) as zf:
                for name in zf.namelist():
                    if "token" in name.lower() or name.endswith(".sqlite3"):
                        fail(f"unexpected file {name} inside {artifact.name}")
        print(f"ok artifact {artifact.name}")
    check_checksums(artifacts, dist / "SHA256SUMS.txt")
    print("ok SHA256SUMS.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="git tag to verify against the package version")
    parser.add_argument(
        "--require-all-standalones",
        action="store_true",
        help="require Linux AMD64/ARM64 and Windows AMD64 standalone executables",
    )
    args = parser.parse_args()
    check_version_file()
    check_manifest()
    if args.tag:
        check_tag(args.tag)
    check_artifacts(require_all_standalones=args.require_all_standalones)
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
