"""PyInstaller entry point for the standalone executable."""

from __future__ import annotations

from joplin_md_sync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
