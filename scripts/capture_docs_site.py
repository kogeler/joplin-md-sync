#!/usr/bin/env python3
"""Capture representative desktop and mobile documentation renders."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import struct
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCREENSHOTS = (
    ("home-desktop.png", "/", 1440, 1100),
    ("home-mobile.png", "/", 390, 844),
    ("contracts-desktop.png", "/contracts/", 1440, 1100),
    ("quick-start-mobile.png", "/user/GETTING_STARTED/", 390, 844),
)


class QuietHandler(SimpleHTTPRequestHandler):
    """Serve generated files without request logging."""

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _browser(explicit: str | None) -> str:
    if explicit:
        candidate = shutil.which(explicit) or explicit
        if Path(candidate).is_file():
            return candidate
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        if candidate := shutil.which(name):
            return candidate
    raise RuntimeError("Chromium or Google Chrome is required for docs-screenshots")


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"browser did not produce a PNG: {path}")
    return struct.unpack(">II", header[16:24])


def capture(site_dir: Path, output_dir: Path, browser: str) -> list[Path]:
    """Serve the built site locally and capture representative viewports."""

    output_dir.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietHandler, directory=str(site_dir.resolve()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    captured: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix="joplin-md-sync-chromium-") as profile:
            for name, route, width, height in SCREENSHOTS:
                target = output_dir / name
                target.unlink(missing_ok=True)
                command = [
                    browser,
                    "--headless",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=3000",
                    "--force-device-scale-factor=1",
                    f"--user-data-dir={profile}",
                    f"--window-size={width},{height}",
                    f"--screenshot={target.resolve()}",
                    f"http://{host}:{port}{route}",
                ]
                if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
                    command.insert(2, "--no-sandbox")
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    raise RuntimeError(
                        f"browser failed for {route}: {result.stderr.strip() or result.stdout.strip()}"
                    )
                if _png_dimensions(target) != (width, height):
                    raise RuntimeError(f"unexpected screenshot dimensions: {target}")
                if target.stat().st_size < 1_000:
                    raise RuntimeError(f"screenshot appears blank: {target}")
                captured.append(target)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return captured


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--output-dir", type=Path, default=Path(".artifacts/docs-screenshots"))
    parser.add_argument("--browser", default=os.environ.get("CHROMIUM"))
    args = parser.parse_args()
    try:
        paths = capture(args.site_dir, args.output_dir, _browser(args.browser))
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"documentation screenshot capture failed: {exc}") from exc
    print("documentation screenshots:")
    for path in paths:
        print(f"  {path}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
