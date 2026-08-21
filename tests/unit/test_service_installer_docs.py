"""Documentation contract for the headless service installer CLI reference."""

import argparse
import runpy
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DOC = ROOT / "docs" / "user" / "SERVICE.md"
SERVICE_SCRIPTS = ROOT / "scripts" / "joplin_terminal_service"


def _installer_parser() -> argparse.ArgumentParser:
    sys.path.insert(0, str(SERVICE_SCRIPTS))
    try:
        namespace = runpy.run_path(str(SERVICE_SCRIPTS / "install_joplin_terminal.py"))
    finally:
        sys.path.pop(0)
    build_parser = cast("Any", namespace["build_parser"])
    return cast("argparse.ArgumentParser", build_parser({}))


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
def test_service_installer_reference_covers_every_public_option() -> None:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    assert "## Installer CLI reference" in contents
    options = {
        option
        for action in _installer_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    for option in options:
        assert f"`{option}" in contents


def test_service_installer_reference_covers_every_environment_override() -> None:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    variables = (
        "JOPLIN_SYNC_TARGET",
        "JOPLIN_SYNC_LOCATION",
        "JOPLIN_SYNC_USERNAME",
        "JOPLIN_SYNC_SECRET_FILE",
        "JOPLIN_S3_ENDPOINT",
        "JOPLIN_S3_REGION",
        "JOPLIN_E2EE_PASSWORD_FILE",
        "JOPLIN_PROFILE_DIR",
        "JOPLIN_API_PORT",
        "JOPLIN_SYNC_INTERVAL",
        "JOPLIN_VERSION",
        "JOPLIN_INSTALL_PREFIX",
        "JOPLIN_MD_SYNC_VERSION",
        "JOPLIN_MCP_PORT",
        "JOPLIN_TERMINAL_ASSET_BASE_URL",
    )
    for variable in variables:
        assert f"`{variable}`" in contents
