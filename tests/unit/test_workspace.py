"""Workspace filesystem primitive contracts."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import workspace


def test_write_file_atomic_replaces_through_same_directory_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "note.md"
    target.write_text("old\n", encoding="utf-8")
    original_replace = workspace.os.replace
    replacements: list[tuple[Path, Path]] = []

    def track_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacements.append((source_path, destination_path))
        assert source_path.parent == destination_path.parent == tmp_path
        assert source_path.name.startswith(".jms-tmp-")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(workspace.os, "replace", track_replace)
    workspace.write_file_atomic(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert len(replacements) == 1
    assert list(tmp_path.glob(".jms-tmp-*")) == []
