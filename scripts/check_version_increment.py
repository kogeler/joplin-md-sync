#!/usr/bin/env python3
"""Check that the working tree version is newer than a Git base revision.

An ordinary change may instead keep a current version that is already
published; its notes accumulate under the changelog's Unreleased section until
a deliberate release advances the version.
"""

from __future__ import annotations

import argparse
import re
import subprocess
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


def read_base_version(base_ref: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:.version"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git show failed"
        raise ValueError(f"cannot read .version from base ref {base_ref!r}: {detail}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument("--base-ref", help="Git revision to compare against")
    base.add_argument("--base-version", help="Exact semantic version to compare against")
    base.add_argument(
        "--published-version",
        help="Already published version that an ordinary change keeps unchanged",
    )
    args = parser.parse_args()

    try:
        current_text = (REPO / ".version").read_text(encoding="utf-8").strip()
        current = parse_version(current_text, "current .version")
        if args.published_version is not None:
            if current != parse_version(args.published_version, "published version"):
                raise ValueError(
                    ".version must equal the already published version for an unreleased "
                    f"change: current {current_text}, published {args.published_version}"
                )
            print(f"ok .version {current_text} is already published; no increment required")
            return 0
        base_text = (
            read_base_version(args.base_ref) if args.base_ref is not None else args.base_version
        )
        assert base_text is not None
        base_version = parse_version(base_text, "base version")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if current <= base_version:
        parser.error(
            f".version must be incremented relative to the comparison base: "
            f"current {current_text}, base {base_text}"
        )

    print(f"ok .version incremented: {base_text} -> {current_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
