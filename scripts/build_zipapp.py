#!/usr/bin/env python3
"""Build dist/joplin-md-sync.pyz with the stdlib zipapp module.

The tool has zero runtime dependencies, so the zipapp contains only the
package itself and runs with any supported CPython:

    python joplin-md-sync.pyz --help
"""

from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "dist" / "joplin-md-sync.pyz"

MAIN = """\
from joplin_md_sync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


def build() -> Path:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jms-zipapp-") as tmp:
        staging = Path(tmp)
        shutil.copytree(
            REPO / "src" / "joplin_md_sync",
            staging / "joplin_md_sync",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Embed the version file so the zipapp resolves it without metadata.
        shutil.copy(REPO / ".version", staging / "joplin_md_sync" / ".version")
        (staging / "__main__.py").write_text(MAIN, encoding="utf-8")
        zipapp.create_archive(
            staging, TARGET, interpreter="/usr/bin/env python3", compressed=True
        )
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"built {path} ({path.stat().st_size} bytes)")
