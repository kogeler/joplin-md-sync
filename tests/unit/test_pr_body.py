"""Regression tests for changelog-managed pull-request bodies."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "pr_body.py"
START = "<!-- joplin-md-sync-changelog:start -->"
END = "<!-- joplin-md-sync-changelog:end -->"


def _run(tmp_path: Path, changelog: str, body: str = "") -> tuple[int, str, str]:
    changelog_path = tmp_path / "CHANGELOG.md"
    body_path = tmp_path / "body.md"
    output = tmp_path / "output.md"
    # Write exact bytes so CRLF fixtures stay CRLF on Windows as well.
    changelog_path.write_bytes(changelog.encode("utf-8"))
    body_path.write_bytes(body.encode("utf-8"))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--changelog",
            str(changelog_path),
            "--existing-body",
            str(body_path),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return (
        result.returncode,
        output.read_text(encoding="utf-8") if output.exists() else "",
        result.stderr,
    )


def test_skips_empty_unreleased_and_preserves_manual_body(tmp_path: Path) -> None:
    code, output, error = _run(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-08-21\n\n- Exact change.\n",
        "Manual context.\n",
    )
    assert code == 0, error
    assert output == (
        f"Manual context.\n\n{START}\n## [1.2.3] - 2026-08-21\n\n- Exact change.\n{END}\n"
    )


def test_replaces_only_managed_block(tmp_path: Path) -> None:
    existing = f"Before.\n\n{START}\n## Old\n\n- Old.\n{END}\n\nAfter.\n"
    code, output, error = _run(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n- Fresh.\n",
        existing,
    )
    assert code == 0, error
    assert output == f"Before.\n\n{START}\n## [Unreleased]\n\n- Fresh.\n{END}\n\nAfter.\n"


def test_rejects_missing_release_entries(tmp_path: Path) -> None:
    code, output, error = _run(tmp_path, "# Changelog\n\n## [Unreleased]\n")
    assert code == 1
    assert output == ""
    assert "bullet entry" in error


def test_prefers_populated_unreleased_without_a_version_change(tmp_path: Path) -> None:
    code, output, error = _run(
        tmp_path,
        (
            "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n### Changed\r\n\r\n- Pending.\r\n\r\n"
            "## [1.2.3] - 2026-08-21\r\n\r\n- Published.\r\n"
        ),
        "Manual context.\n",
    )
    assert code == 0, error
    assert output == (
        f"Manual context.\n\n{START}\n## [Unreleased]\n\n### Changed\n\n- Pending.\n{END}\n"
    )


def test_rejects_malformed_or_reserved_markers(tmp_path: Path) -> None:
    changelog = "# Changelog\n\n## [Unreleased]\n\n- Fresh.\n"
    for body in (
        f"Manual.\n\n{START}\n",
        f"{END}\n\n{START}\n",
        f"{START}\n{END}\n{START}\n{END}\n",
    ):
        code, output, error = _run(tmp_path, changelog, body)
        assert code == 1
        assert output == ""
        assert "invalid managed markers" in error

    code, output, error = _run(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n- {START}\n")
    assert code == 1
    assert output == ""
    assert "reserved marker" in error


def test_rejects_an_oversized_existing_body(tmp_path: Path) -> None:
    code, output, error = _run(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n- Fresh.\n",
        "x" * 65_537,
    )
    assert code == 1
    assert output == ""
    assert "size limit" in error
