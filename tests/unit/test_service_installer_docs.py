"""Documentation contract for the headless service installer CLI reference."""

import argparse
import http.server
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DOC = ROOT / "docs" / "user" / "JOPLIN_HEADLESS_SERVICE.md"
SERVICE_SCRIPTS = ROOT / "scripts" / "joplin_terminal_service"
INSTALLER_URL = (
    "https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/"
    "scripts/joplin_terminal_service/install_joplin_terminal.py"
)


def _remote_command(heading: str) -> str:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    section = contents.split(f"### {heading}\n", 1)[1].split("\n### ", 1)[0]
    return section.split("```bash\n", 1)[1].split("```", 1)[0].strip()


def _remote_arguments(heading: str) -> argparse.Namespace:
    words = shlex.split(_remote_command(heading).replace("\\\n", ""))
    assert words[:4] == ["set", "-o", "pipefail", "curl"]
    pipe = words.index("|")
    assert words[pipe + 1 : pipe + 3] == ["python3", "-"]
    assert INSTALLER_URL in words[4:pipe]
    assert words[4:6] == ["--proto", "=https"]
    assert {"--tlsv1.2", "--fail", "--show-error", "--location"} <= set(words[4:pipe])
    namespace = _installer_namespace()
    args = namespace["build_parser"]({}).parse_args(words[pipe + 3 :])
    namespace["validate_args"](args)
    return cast("argparse.Namespace", args)


def test_service_guide_starts_with_runbook_then_layout_reference_and_advanced_use() -> None:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    assert contents.startswith("# Joplin Headless Service\n")
    assert [line for line in contents.splitlines() if line.startswith("## ")] == [
        "## Quick runbook",
        "## What is installed",
        "## Installer CLI reference",
        "## Advanced installation and maintenance",
    ]
    runbook = contents.split("## What is installed", 1)[0]
    assert runbook.index("### Install with Nextcloud") < runbook.index("### Install with S3")
    assert runbook.index("### Install with S3") < runbook.index("### Remove the local installation")
    assert "This deletes the local notes profile" in runbook
    assert "It does not delete remote" in runbook


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
def test_nextcloud_runbook_example_defaults_to_hidden_password_prompt() -> None:
    args = _remote_arguments("Install with Nextcloud")
    assert args.sync_target == "nextcloud"
    assert args.sync_location
    assert args.sync_username
    assert args.sync_secret_file is None
    assert args.e2ee_password_file is None
    assert not args.non_interactive


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
def test_custom_s3_example_defaults_to_interactive_credentials() -> None:
    args = _remote_arguments("Install with S3")
    assert args.sync_target == "s3"
    assert args.s3_endpoint
    assert args.s3_region
    assert args.sync_username is None
    assert args.sync_username_file is None
    assert args.sync_secret_file is None
    assert not args.non_interactive


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
def test_clean_runbook_example_needs_no_confirmation_flag() -> None:
    args = _remote_arguments("Remove the local installation")
    assert args.purge
    assert not args.yes
    assert not args.non_interactive


@pytest.fixture
def installer_bundle_url():
    def handler(*args, **kwargs):
        return http.server.SimpleHTTPRequestHandler(*args, directory=str(SERVICE_SCRIPTS), **kwargs)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
@pytest.mark.parametrize(
    "heading", ["Install with Nextcloud", "Install with S3", "Remove the local installation"]
)
def test_remote_runbook_pipeline_executes_real_installer_dry_run_offline(
    tmp_path: Path,
    heading: str,
    installer_bundle_url: str,
) -> None:
    """Exercise Bash/stdin bootstrap without network, a user bus, secrets, or writes."""
    shell = shutil.which("bash")
    assert shell is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # No real host tools are on PATH. Dependency shims allow version checks only.
    for name in ("node", "npm", "systemctl", "loginctl"):
        shim = bin_dir / name
        shim.write_text(
            '#!/bin/sh\nif [ "$#" = 1 ] && [ "$1" = --version ]; then\n'
            "  printf '22.0.0\\n'\nelse\n  exit 99\nfi\n",
            encoding="utf-8",
        )
        shim.chmod(0o700)
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!/bin/sh\nexec /bin/cat {shlex.quote(str(SERVICE_SCRIPTS / 'install_joplin_terminal.py'))}\n",
        encoding="utf-8",
    )
    curl.chmod(0o700)
    (bin_dir / "python3").symlink_to(sys.executable)
    service_home = tmp_path / "home"
    service_home.mkdir()
    result = subprocess.run(
        [shell, "--noprofile", "--norc", "-c", _remote_command(heading) + " --dry-run"],
        env={
            "HOME": str(service_home),
            "PATH": str(bin_dir),
            "PYTHONPATH": str(SERVICE_SCRIPTS),
            "PYTHONDONTWRITEBYTECODE": "1",
            "JOPLIN_TERMINAL_ASSET_BASE_URL": installer_bundle_url,
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run:" in result.stderr
    assert not list(service_home.iterdir())


def _installer_namespace() -> dict[str, Any]:
    sys.path.insert(0, str(SERVICE_SCRIPTS))
    try:
        from joplin_terminal_installer import cli

        namespace = {"build_parser": cli.build_parser, "validate_args": cli.validate_args}
    finally:
        sys.path.pop(0)
    return namespace


def _installer_parser() -> argparse.ArgumentParser:
    return cast("argparse.ArgumentParser", _installer_namespace()["build_parser"]({}))


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
        "JOPLIN_SYNC_USERNAME_FILE",
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
        "JOPLIN_TERMINAL_INSTALLER_REF",
    )
    for variable in variables:
        assert f"`{variable}`" in contents
