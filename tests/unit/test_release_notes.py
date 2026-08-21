"""Tests for deterministic release notes generated from the changelog."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "release_notes.py"


def _run(root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_uses_only_the_exact_current_changelog_section(tmp_path: Path) -> None:
    (tmp_path / ".version").write_text("1.2.3\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- Future.\n\n"
        "## [1.2.3] - 2026-08-21\n\n### Added\n\n- Current.\n\n"
        "## [1.2.2] - 2026-08-20\n\n- Previous.\n",
        encoding="utf-8",
    )
    output = tmp_path / "notes.md"

    result = _run(tmp_path, output)

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == (
        "### Added\n\n- Current.\n\n"
        "Full changelog: https://github.com/kogeler/joplin-md-sync/blob/v1.2.3/CHANGELOG.md\n"
    )


def test_committed_release_metadata_produces_notes(tmp_path: Path) -> None:
    output = tmp_path / "notes.md"
    result = _run(ROOT, output)
    assert result.returncode == 0, result.stderr
    assert "Reframe the GitHub and PyPI project page" in output.read_text(encoding="utf-8")
