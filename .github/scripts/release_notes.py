# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Generate release notes for the version declared in .version."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_URL = "https://github.com/kogeler/joplin-md-sync"
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class ReleaseNotesError(ValueError):
    """Raised when current release metadata cannot produce exact notes."""


def _version(root: Path) -> str:
    try:
        version = (root / ".version").read_text(encoding="utf-8").strip()
    except OSError as err:
        raise ReleaseNotesError(f"cannot read .version: {err}") from err
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseNotesError(".version must contain exactly one stable X.Y.Z version")
    return version


def _changelog_body(root: Path, version: str) -> str:
    try:
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as err:
        raise ReleaseNotesError(f"cannot read CHANGELOG.md: {err}") from err
    heading = re.compile(
        rf"^## \[{re.escape(version)}\] - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$",
        re.MULTILINE,
    )
    matches = list(heading.finditer(changelog))
    if len(matches) != 1:
        raise ReleaseNotesError(
            f"CHANGELOG.md must contain exactly one '## [{version}] - YYYY-MM-DD' section"
        )
    start = matches[0].end()
    next_heading = re.search(r"^## ", changelog[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading is not None else len(changelog)
    body = changelog[start:end].strip()
    if not body or re.search(r"^- ", body, re.MULTILINE) is None:
        raise ReleaseNotesError(f"CHANGELOG.md section {version} must contain release entries")
    return body


def _run(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    version = _version(root)
    body = _changelog_body(root, version)
    args.output.write_text(
        f"{body}\n\nFull changelog: {REPOSITORY_URL}/blob/v{version}/CHANGELOG.md\n",
        encoding="utf-8",
    )
    print(version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    try:
        _run(parser.parse_args())
    except (OSError, ReleaseNotesError) as err:
        print(f"release notes error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
