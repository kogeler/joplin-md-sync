#!/usr/bin/env python3
"""Refresh or check the integrity metadata for the remote installer bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(*, write: bool = False) -> None:
    files = [
        *sorted((ROOT / "joplin_terminal_installer").glob("*.py")),
        ROOT / "joplin_terminal_common.py",
        ROOT / "run_joplin_terminal.py",
        ROOT / "systemd/joplin-terminal.service",
        ROOT / "systemd/joplin-md-sync.service",
    ]
    records = {}
    for path in files:
        data = path.read_bytes()
        records[str(path.relative_to(ROOT))] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    manifest = (
        json.dumps({"schema": 1, "files": records}, indent=2, sort_keys=True) + "\n"
    ).encode()
    entry = ROOT / "install_joplin_terminal.py"
    updated, count = re.subn(
        r'^BUNDLE_MANIFEST_SHA256 = ".*"$',
        'BUNDLE_MANIFEST_SHA256 = "' + hashlib.sha256(manifest).hexdigest() + '"',
        entry.read_text(),
        flags=re.MULTILINE,
    )
    assert count == 1
    destination = ROOT / "installer-bundle.json"
    if write:
        destination.write_bytes(manifest)
        entry.write_text(updated)
    elif (
        not destination.is_file()
        or destination.read_bytes() != manifest
        or entry.read_text() != updated
    ):
        raise SystemExit(
            "Installer bundle is stale: run python3 scripts/joplin_terminal_service/build_bundle_manifest.py --write"
        )
    print(f"Installer bundle: {len(records)} verified source/assets")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="refresh metadata after source changes"
    )
    build(write=parser.parse_args().write)
