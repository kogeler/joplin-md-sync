"""Joplin Terminal installation: assets."""

from __future__ import annotations

import logging
from pathlib import Path

from .common import (
    ToolError,
)

LOG = logging.getLogger("joplin-terminal-installer")


ASSET_ROOT = Path(__file__).resolve().parent.parent


def read_project_asset(relative_path: str, *, limit: int = 2 * 1024 * 1024) -> bytes:
    """Read an asset from the local or verified downloaded bundle."""
    path = ASSET_ROOT / relative_path
    if not path.is_relative_to(ASSET_ROOT) or ".." in Path(relative_path).parts:
        raise ToolError("invalid installer asset path")
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        raise ToolError(f"installer bundle lacks required asset {relative_path}") from None
    if len(data) > limit:
        raise ToolError(f"installer asset is unexpectedly large: {relative_path}")
    return data
