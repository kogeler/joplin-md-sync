"""Static checks for metadata rendered on the PyPI project page."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_pypi_readme_uses_only_portable_absolute_links() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]*\]\(([^)]+)\)", readme)
    assert links
    assert all(link.startswith(("https://", "http://")) for link in links)


def test_pypi_metadata_exposes_public_project_routes() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for expected in (
        'Homepage = "https://joplin-mcp.romancello.net/"',
        'Documentation = "https://joplin-mcp.romancello.net/"',
        'Repository = "https://github.com/kogeler/joplin-md-sync"',
        'Issues = "https://github.com/kogeler/joplin-md-sync/issues"',
        'Changelog = "https://github.com/kogeler/joplin-md-sync/blob/main/CHANGELOG.md"',
    ):
        assert expected in pyproject
