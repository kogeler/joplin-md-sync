"""Cross-platform-safe filename generation and path safety checks.

Filenames are cosmetic: identity always lives in the metadata header and the
state database. Rules (docs/WORKSPACE_FORMAT.md):

* note files:   ``<sanitized title>--<first 8 chars of note id>.md``
* notebook dirs: ``<sanitized title>`` (plus ``--<first 8 of id>`` only when
  two sibling folders collide case-insensitively).
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from pathlib import Path

# Characters invalid on Windows (superset of POSIX-problematic characters).
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

# Windows reserved device names (case-insensitive, with or without extension).
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_MAX_COMPONENT_LEN = 80  # characters kept from the sanitized title


def sanitize_component(title: str) -> str:
    """Turn an arbitrary note/notebook title into a safe path component."""
    # Strip control characters and characters invalid on Windows.
    name = _INVALID_CHARS_RE.sub("_", title)
    # Collapse whitespace runs into single spaces; drop leading/trailing.
    name = re.sub(r"\s+", " ", name).strip()
    # Windows forbids trailing dots and spaces.
    name = name.rstrip(". ")
    # Never emit names that only differ by combining marks rendering oddly.
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cf")
    if len(name) > _MAX_COMPONENT_LEN:
        name = name[:_MAX_COMPONENT_LEN].rstrip(". ")
    if not name:
        name = "untitled"
    # Reserved device names: CON, CON.md, con.txt are all invalid on Windows.
    stem = name.split(".", 1)[0]
    if stem.upper() in _RESERVED:
        name = f"_{name}"
    # A component of only dots would be "." / ".." path traversal.
    if set(name) <= {"."}:
        name = "untitled"
    return name


def note_filename(title: str, note_id: str) -> str:
    """Canonical filename for a managed note."""
    return f"{sanitize_component(title)}--{note_id[:8]}.md"


def folder_dirname(title: str, folder_id: str, taken_casefold: set[str]) -> str:
    """Canonical directory name for a notebook among its siblings.

    ``taken_casefold`` holds casefolded names already used by siblings; on a
    case-insensitive collision the id suffix disambiguates deterministically.
    """
    base = sanitize_component(title)
    if base.casefold() in taken_casefold:
        base = f"{base}--{folder_id[:8]}"
    return base


def is_within_root(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves inside ``root`` (both resolved)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_rel_path(rel_path: str) -> bool:
    """Reject absolute paths, drive letters, and ``..`` traversal in stored paths."""
    if not rel_path or rel_path.startswith(("/", "\\")):
        return False
    if re.match(r"^[A-Za-z]:", rel_path):
        return False
    parts = posixpath.normpath(rel_path.replace("\\", "/")).split("/")
    return ".." not in parts and parts[0] not in ("", ".")
