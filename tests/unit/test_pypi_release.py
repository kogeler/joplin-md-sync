"""Tests for exact PyPI recovery verification."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "verify_pypi_release.py"
VERSION = "1.2.3"


def _files(dist: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, packagetype in (
        (f"joplin_md_sync-{VERSION}-py3-none-any.whl", "bdist_wheel"),
        (f"joplin_md_sync-{VERSION}.tar.gz", "sdist"),
    ):
        path = dist / name
        path.write_bytes(f"contents:{name}".encode())
        result.append(
            {
                "filename": name,
                "packagetype": packagetype,
                "size": path.stat().st_size,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "yanked": False,
            }
        )
    return result


def _run(tmp_path: Path, mutate: Any = None) -> subprocess.CompletedProcess[str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    metadata = {"info": {"name": "joplin-md-sync", "version": VERSION}, "urls": _files(dist)}
    if mutate is not None:
        mutate(metadata, dist)
    metadata_path = tmp_path / "pypi.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project",
            "joplin-md-sync",
            "--version",
            VERSION,
            "--dist-dir",
            str(dist),
            "--metadata-file",
            str(metadata_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_accepts_exact_local_and_pypi_distribution_inventory(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(" matches PyPI (") == 2


def test_rejects_a_local_distribution_that_differs_from_pypi(tmp_path: Path) -> None:
    def mutate(metadata: dict[str, Any], dist: Path) -> None:
        del metadata
        (dist / f"joplin_md_sync-{VERSION}.tar.gz").write_bytes(b"changed")

    result = _run(tmp_path, mutate)
    assert result.returncode == 1
    assert "does not match PyPI" in result.stderr


def test_rejects_an_unexpected_or_yanked_pypi_distribution(tmp_path: Path) -> None:
    def mutate(metadata: dict[str, Any], dist: Path) -> None:
        del dist
        metadata["urls"][0]["yanked"] = True

    result = _run(tmp_path, mutate)
    assert result.returncode == 1
    assert "is yanked" in result.stderr
