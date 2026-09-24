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
AUDIENCES = ("dev", "test", "package", "docs")
LOCKS = tuple(f"requirements-{audience}.txt" for audience in AUDIENCES)
INPUTS = tuple(f"requirements-{audience}.in" for audience in AUDIENCES)


def _copy_inputs(destination: Path) -> None:
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    for name in INPUTS + LOCKS:
        shutil.copy2(ROOT / name, destination / name)


def _replace_input(root: Path, pattern: str, replacement: str) -> None:
    path = root / "requirements-dev.in"
    content = path.read_text(encoding="utf-8")
    updated = re.sub(pattern, replacement, content, count=1, flags=re.MULTILINE)
    assert updated != content
    path.write_text(updated, encoding="utf-8")


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


def test_snapshot_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_result = _run(ROOT, first)
    second_result = _run(ROOT, second)

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first.read_bytes() == second.read_bytes()


def test_rejects_direct_version_drift(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    _replace_input(tmp_path, r"^ruff==\S+$", "ruff==0.0.1")

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "direct dependency versions differ" in result.stderr


def test_rejects_direct_dependency_missing_from_lock(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    _replace_input(tmp_path, r"^ruff==\S+$", "missing-package==1.0.0")

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "direct dependencies missing from lock: missing-package" in result.stderr


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
    _replace_input(tmp_path, r"^ruff==(\S+)$", r"ruff>=\1")

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "dependency must be one exact PyPI pin" in result.stderr


def test_rejects_included_files_and_options_in_independent_inputs(tmp_path: Path) -> None:
    for option in ("-r requirements-test.in", "--index-url https://example.invalid/simple"):
        _copy_inputs(tmp_path)
        _replace_input(tmp_path, r"^(ruff==\S+)$", rf"\1\n{option}")

        result = _run(tmp_path, tmp_path / "snapshot.json")

        assert result.returncode == 1
        assert "cannot include files or options" in result.stderr


def test_rejects_dependency_versions_in_project_metadata(tmp_path: Path) -> None:
    _copy_inputs(tmp_path)
    project = tmp_path / "pyproject.toml"
    content = project.read_text(encoding="utf-8")
    project.write_text(
        content.replace(
            "[project.urls]",
            '[project.optional-dependencies]\ndev = ["fixture-package==1.0.0"]\n\n[project.urls]',
        ),
        encoding="utf-8",
    )

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "must exist only in requirements inputs" in result.stderr

    project.write_text(
        content.replace("dependencies = []", 'dependencies = ["fixture-package==1.0.0"]'),
        encoding="utf-8",
    )

    result = _run(tmp_path, tmp_path / "snapshot.json")

    assert result.returncode == 1
    assert "dedicated reviewed lock policy" in result.stderr
