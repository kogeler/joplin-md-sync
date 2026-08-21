#!/usr/bin/env python3
"""Verify that an existing PyPI release exactly matches local distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT = "joplin-md-sync"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class PyPIReleaseError(ValueError):
    """Raised when published PyPI metadata is incomplete or conflicting."""


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _expected_files(version: str) -> dict[str, str]:
    return {
        f"joplin_md_sync-{version}-py3-none-any.whl": "bdist_wheel",
        f"joplin_md_sync-{version}.tar.gz": "sdist",
    }


def _published_files(metadata: Any, *, project: str, version: str) -> dict[str, tuple[int, str]]:
    if not isinstance(metadata, dict):
        raise PyPIReleaseError("PyPI metadata must be an object")
    info = metadata.get("info")
    urls = metadata.get("urls")
    if not isinstance(info, dict) or not isinstance(urls, list):
        raise PyPIReleaseError("PyPI metadata is missing info or urls")
    name = info.get("name")
    if not isinstance(name, str) or _canonical_name(name) != _canonical_name(project):
        raise PyPIReleaseError(f"PyPI project name {name!r} does not match {project!r}")
    if info.get("version") != version:
        raise PyPIReleaseError(
            f"PyPI version {info.get('version')!r} does not match {version!r}"
        )

    expected = _expected_files(version)
    if len(urls) != len(expected):
        raise PyPIReleaseError("PyPI release has an unexpected file inventory")
    published: dict[str, tuple[int, str]] = {}
    for item in urls:
        if not isinstance(item, dict):
            raise PyPIReleaseError("PyPI file metadata must be an object")
        filename = item.get("filename")
        packagetype = item.get("packagetype")
        size = item.get("size")
        digests = item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(filename, str) or expected.get(filename) != packagetype:
            raise PyPIReleaseError(f"unexpected PyPI distribution {filename!r}")
        if type(size) is not int or size <= 0:
            raise PyPIReleaseError(f"invalid size for PyPI distribution {filename!r}")
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise PyPIReleaseError(f"invalid SHA-256 for PyPI distribution {filename!r}")
        if item.get("yanked") is not False:
            raise PyPIReleaseError(f"PyPI distribution {filename!r} is yanked")
        if filename in published:
            raise PyPIReleaseError(f"duplicate PyPI distribution {filename!r}")
        published[filename] = (size, digest)
    if set(published) != set(expected):
        raise PyPIReleaseError("PyPI release is missing an expected distribution")
    return published


def _load_metadata(*, project: str, version: str, metadata_file: Path | None) -> Any:
    if metadata_file is not None:
        return json.loads(metadata_file.read_text(encoding="utf-8"))
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "joplin-md-sync-release-ci"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.load(response)
    except urllib.error.HTTPError as err:
        raise PyPIReleaseError(f"PyPI returned HTTP {err.code} for {project} {version}") from err
    except urllib.error.URLError as err:
        raise PyPIReleaseError(f"cannot read PyPI metadata: {err.reason}") from err


def verify(
    *,
    project: str,
    version: str,
    dist_dir: Path,
    metadata_file: Path | None = None,
) -> None:
    published = _published_files(
        _load_metadata(project=project, version=version, metadata_file=metadata_file),
        project=project,
        version=version,
    )
    expected_names = set(_expected_files(version))
    local_files = {path.name: path for path in dist_dir.iterdir() if path.is_file()}
    if set(local_files) != expected_names:
        raise PyPIReleaseError(
            f"local distribution inventory {sorted(local_files)} != {sorted(expected_names)}"
        )
    for name in sorted(expected_names):
        path = local_files[name]
        expected_size, expected_digest = published[name]
        actual_size = path.stat().st_size
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_size != expected_size or actual_digest != expected_digest:
            raise PyPIReleaseError(f"local distribution {name!r} does not match PyPI")
        print(f"ok {name} matches PyPI ({actual_digest})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--version")
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--metadata-file", type=Path)
    args = parser.parse_args()
    try:
        version = args.version or (args.root / ".version").read_text(encoding="utf-8").strip()
        if re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) is None:
            raise PyPIReleaseError("version must be stable X.Y.Z")
        verify(
            project=args.project,
            version=version,
            dist_dir=args.dist_dir,
            metadata_file=args.metadata_file,
        )
    except (OSError, PyPIReleaseError, json.JSONDecodeError) as err:
        print(f"PyPI release verification failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
