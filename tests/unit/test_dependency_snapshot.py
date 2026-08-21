"""Regression tests for the exact GitHub dependency-submission snapshot."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SNAPSHOT_SCRIPT = ROOT / ".github" / "scripts" / "dependency_snapshot.py"
LOCKS = (
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements-package.txt",
    "requirements-docs.txt",
)


def _copy_inputs(destination: Path) -> None:
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    for name in LOCKS:
        shutil.copy2(ROOT / name, destination / name)


def _run(root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SNAPSHOT_SCRIPT),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_builds_all_exact_lock_manifests(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    output = tmp_path / "snapshot.json"

    result = _run(tmp_path, output)

    assert result.returncode == 0, result.stderr
    manifests = json.loads(output.read_text(encoding="utf-8"))["manifests"]
    assert set(manifests) == set(LOCKS)
    assert manifests["requirements-dev.txt"]["resolved"]["ruff"]["relationship"] == "direct"
    assert manifests["requirements-dev.txt"]["resolved"]["mypy"]["relationship"] == "direct"
    assert manifests["requirements-test.txt"]["resolved"]["pytest"]["relationship"] == "direct"
    assert manifests["requirements-package.txt"]["resolved"]["pyinstaller"]["relationship"] == (
        "direct"
    )
    assert manifests["requirements-docs.txt"]["resolved"]["mkdocs-material"]["scope"] == (
        "development"
    )
    assert manifests["requirements-dev.txt"]["resolved"]["packaging"]["relationship"] == (
        "indirect"
    )


def test_rejects_direct_version_drift(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    project = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        re.sub(r'"ruff==[^"]+"', '"ruff==0.0.1"', project), encoding="utf-8"
    )

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "direct dependency versions differ" in result.stderr


def test_rejects_hashless_lock_entry(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    path = tmp_path / "requirements-dev.txt"
    lock = path.read_text(encoding="utf-8")
    lock = re.sub(
        r"(ruff==[^\n]+\\\n)(?:[ \t]+--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+",
        r"\1",
        lock,
        count=1,
    )
    path.write_text(lock, encoding="utf-8")

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "has no SHA-256 hash" in result.stderr


def test_rejects_unpinned_direct_dependency(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    project = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        re.sub(r'"ruff==([^"]+)"', r'"ruff>=\1"', project), encoding="utf-8"
    )

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "dependency must be one exact PyPI pin" in result.stderr
