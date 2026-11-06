#!/usr/bin/env python3
"""Run the local installer package or download a verified bundle into /tmp."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

DEFAULT_REF = "main"
BUNDLE_MANIFEST_SHA256 = "909beab7a710e07f13acb5ae04c22aee574489e204093670f65bab918d8a1772"
PACKAGE = "joplin_terminal_installer"
MAX_FILE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024


class BootstrapError(RuntimeError):
    """A missing or inconsistent installer bundle; no setup has been started."""


def source_selection(argv: list[str] | None = None) -> tuple[str, str, bool]:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument(
        "--installer-ref",
        "--ref",
        default=os.environ.get("JOPLIN_TERMINAL_INSTALLER_REF", DEFAULT_REF),
    )
    options, _rest = parser.parse_known_args(arguments)
    ref = options.installer_ref
    if not ref or any(character.isspace() or ord(character) < 0x20 for character in ref):
        raise BootstrapError("installer ref must be a nonempty Git branch, tag or commit SHA")
    encoded = urllib.parse.quote(ref, safe="")
    override = os.environ.get("JOPLIN_TERMINAL_ASSET_BASE_URL")
    explicit = "JOPLIN_TERMINAL_INSTALLER_REF" in os.environ or any(
        token.split("=", 1)[0] in {"--installer-ref", "--ref"} for token in arguments
    )
    if override and "{ref}" in override:
        base = override.replace("{ref}", encoded)
    elif override and explicit:
        url = urllib.parse.urlsplit(override)
        components = url.path.strip("/").split("/")
        if (
            url.scheme == "https"
            and url.netloc == "raw.githubusercontent.com"
            and len(components) >= 3
        ):
            base = (
                "https://raw.githubusercontent.com/"
                + "/".join(components[:2])
                + "/"
                + encoded
                + "/scripts/joplin_terminal_service"
            )
        else:
            raise BootstrapError(
                "a custom JOPLIN_TERMINAL_ASSET_BASE_URL must contain {ref} when selecting --installer-ref"
            )
    else:
        base = (
            override
            or "https://raw.githubusercontent.com/kogeler/joplin-md-sync/"
            + encoded
            + "/scripts/joplin_terminal_service"
        )
    return ref, base.rstrip("/"), explicit


def download(relative: str, limit: int, base_url: str) -> bytes:
    try:
        request = urllib.request.Request(
            base_url + "/" + relative,
            headers={"User-Agent": "joplin-terminal-installer"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(limit + 1)
    except (OSError, ValueError, http.client.HTTPException):
        # URLError is an OSError; ValueError covers malformed base URLs and
        # HTTPException truncated responses, so no traceback escapes setup.
        raise BootstrapError("could not download the installer bundle") from None
    if len(data) > limit:
        raise BootstrapError("installer bundle exceeds its download limit")
    return data


def download_bundle(root: Path, base_url: str) -> None:
    raw = download("installer-bundle.json", 64 * 1024, base_url)
    if hashlib.sha256(raw).hexdigest() != BUNDLE_MANIFEST_SHA256:
        raise BootstrapError(
            "installer entry/bundle version mismatch; download the entry script from the same --installer-ref"
        )
    try:
        manifest = json.loads(raw)
        records = manifest["files"]
        if manifest["schema"] != 1 or not isinstance(records, dict) or not 1 <= len(records) <= 64:
            raise ValueError
        total = 0
        for relative, record in records.items():
            path = Path(relative)
            size, digest = record["size"], record["sha256"]
            if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in relative:
                raise ValueError
            if not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
                raise ValueError
            total += size
            if total > MAX_BUNDLE_BYTES:
                raise ValueError
            data = download(relative, size, base_url)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise BootstrapError("installer bundle checksum mismatch; setup was not started")
            destination = root / path
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with destination.open("xb") as output:
                os.chmod(destination, 0o600)
                output.write(data)
    except (KeyError, TypeError, ValueError):
        raise BootstrapError("invalid installer bundle manifest") from None


@contextmanager
def installer_application(argv: list[str] | None = None):
    ref, base_url, explicit_ref = source_selection(argv)
    filename = globals().get("__file__", "<stdin>")
    local = None if filename.startswith("<") else Path(filename).resolve().parent
    if not explicit_ref and local is not None and (local / PACKAGE / "application.py").is_file():
        sys.path.insert(0, str(local))
        try:
            yield importlib.import_module(PACKAGE + ".application")
        finally:
            sys.path.remove(str(local))
        return
    with tempfile.TemporaryDirectory(prefix="joplin-installer-", dir="/tmp") as temporary:
        label = (
            ref
            if explicit_ref or not os.environ.get("JOPLIN_TERMINAL_ASSET_BASE_URL")
            else "custom bundle URL"
        )
        print(f"Installer source: {label}", file=sys.stderr)
        root = Path(temporary)
        download_bundle(root, base_url)
        previous = {
            name: value
            for name, value in sys.modules.items()
            if name == PACKAGE or name.startswith(PACKAGE + ".")
        }
        for name in previous:
            del sys.modules[name]
        sys.path.insert(0, str(root))
        try:
            yield importlib.import_module(PACKAGE + ".application")
        finally:
            sys.path.remove(str(root))
            for name in list(sys.modules):
                if name == PACKAGE or name.startswith(PACKAGE + "."):
                    del sys.modules[name]
            sys.modules.update(previous)


def main(argv: list[str] | None = None) -> int:
    try:
        with installer_application(argv) as application:
            return application.main(argv)
    except BootstrapError as exc:
        print(f"Installer: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
