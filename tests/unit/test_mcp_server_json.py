"""Tests for the MCP Registry server.json generator."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import mcp_server_json

ROOT = Path(__file__).parents[2]


def _project(tmp_path: Path, readme: str) -> Path:
    for name in ("pyproject.toml", ".version"):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path


def test_server_requires_the_readme_ownership_marker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mcp-name"):
        mcp_server_json.server(_project(tmp_path, "# Project\n"))


def test_server_publishes_the_release_package_it_describes(tmp_path: Path) -> None:
    marker = f"<!-- mcp-name: {mcp_server_json.NAME} -->\n"
    document = mcp_server_json.server(_project(tmp_path, marker))

    (package,) = document["packages"]  # type: ignore[misc]
    assert package["version"] == document["version"]
    assert len(str(document["description"])) <= 100
