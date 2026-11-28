"""Shipped service definitions start a command the current CLI accepts."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICE_SCRIPTS = REPO / "scripts" / "joplin_terminal_service"
SYSTEMD = SERVICE_SCRIPTS / "systemd"
sys.path.insert(0, str(REPO / "src"))

from joplin_md_sync.cli import build_parser  # noqa: E402
from tests.oauth.helpers import OAuthArguments  # noqa: E402


def test_only_one_adapter_unit_is_shipped() -> None:
    adapter_units = sorted(path.name for path in SYSTEMD.glob("joplin-md-sync*.service"))
    assert adapter_units == ["joplin-md-sync.service"]
    content = (SYSTEMD / adapter_units[0]).read_text(encoding="utf-8")
    assert "Description=Joplin Markdown Sync MCP service" in content
    assert "ExecStart={exec_start}" in content
    assert "UMask=0077" in content
    assert "Bearer " not in content


def _exec_start(unit: str) -> list[str]:
    (line,) = (line for line in unit.splitlines() if line.startswith("ExecStart="))
    return [value.replace("%%", "%") for value in shlex.split(line.removeprefix("ExecStart="))]


@pytest.mark.skipif(sys.platform == "win32", reason="headless installer supports Linux only")
def test_installer_unit_command_is_accepted_by_the_current_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SERVICE_SCRIPTS))
    from joplin_terminal_installer import cli as installer_cli
    from joplin_terminal_installer import service

    env = {"HOME": str(tmp_path)}
    oauth = OAuthArguments(tmp_path)
    plain = installer_cli.build_parser(env).parse_args([])
    paths = installer_cli.build_paths(plain, env)
    enabled = installer_cli.build_parser(env).parse_args(oauth.argv())
    template = (SYSTEMD / "joplin-md-sync.service").read_text(encoding="utf-8")
    for options in (
        {},
        {"allow_remote": True},
        {
            "github_oauth_args": installer_cli.github_oauth_arguments(enabled),
            "oauth_state_path": tmp_path / "state",
        },
    ):
        unit = service.render_adapter_unit(
            template, paths=paths, api_port=41185, mcp_port=8765, **options
        )
        command = _exec_start(unit)
        assert command[0] == str(paths.mcp_binary)
        # argparse exits on any option the current release no longer accepts.
        args = build_parser().parse_args(command[1:])
        assert (args.command, args.mcp_command) == ("mcp", "serve")
        assert args.auth_token_file == str(paths.mcp_auth_token_file)


def test_windows_task_command_is_accepted_by_the_current_cli() -> None:
    assert not (REPO / "examples" / "windows" / "install-mcp-task.ps1").exists()
    content = (REPO / "examples" / "windows" / "install-service-task.ps1").read_text(
        encoding="utf-8"
    )
    assert '[string]$TaskName = "joplin-md-sync"' in content
    assert "if ($McpAuthTokenFile)" in content
    # Rebuild the task argument list with every optional part enabled.
    region = content[content.index("$arguments = @(") : content.index("$currentUser")]
    region = re.sub(r"if \(\$\w+\)", "", region)
    samples = {
        "JoplinTokenFile": r"C:\Users\agent\joplin-token",
        "McpAuthTokenFile": r"C:\Users\agent\mcp-token",
        "Port": "8765",
    }
    command = [
        literal or samples[variable]
        for literal, variable in re.findall(r'"([^"$]+)"|\$(\w+)', region)
        if variable != "arguments"
    ]
    assert command[:2] == ["mcp", "serve"]
    args = build_parser().parse_args(command)
    assert args.token_file == samples["JoplinTokenFile"]
    assert args.auth_token_file == samples["McpAuthTokenFile"]
    assert args.mcp_port == 8765


def test_source_distribution_includes_complete_service_installer() -> None:
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")
    for name in (
        "collect_joplin_debug.sh",
        "install_joplin_terminal.py",
        "joplin_terminal_common.py",
        "run_joplin_terminal.py",
    ):
        assert f"include scripts/joplin_terminal_service/{name}" in manifest
    assert "recursive-include scripts/joplin_terminal_service/systemd *.service" in manifest
