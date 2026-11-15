#!/usr/bin/env python3
"""Check that the working tree version advances past its comparison base.

A changed version must exceed the base. An unchanged version is accepted only
while it is already published, so ordinary changes accumulate notes under the
changelog's Unreleased section; while it is unpublished it must exceed the
latest published release.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
Version = tuple[int, int, int]


def parse_version(value: str, source: str) -> Version:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"{source} does not contain a plain semver version: {value!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-version", required=True, help="Version of the comparison base")
    state = parser.add_mutually_exclusive_group(required=True)
    state.add_argument(
        "--published", action="store_true", help="The current version is already published"
    )
    state.add_argument(
        "--latest-release",
        help="Latest published release, given while the current version is unpublished",
    )
    args = parser.parse_args()

    try:
        current_text = (REPO / ".version").read_text(encoding="utf-8").strip()
        current = parse_version(current_text, "current .version")
        base_text = args.base_version
        base = parse_version(base_text, "base version")
        if current == base and args.published:
            print(f"ok .version {current_text} is already published; no increment required")
            return 0
        if current == base:
            base_text = args.latest_release
            base = parse_version(base_text, "latest release")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if current <= base:
        parser.error(
            f".version must be incremented relative to the comparison base: "
            f"current {current_text}, base {base_text}"
        )
    print(f"ok .version incremented: {base_text} -> {current_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
