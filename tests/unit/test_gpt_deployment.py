"""The headless installer ships one combined adapter service."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_only_one_combined_adapter_unit_is_shipped() -> None:
    systemd_dir = REPO / "scripts" / "joplin_terminal_service" / "systemd"
    adapter_units = sorted(path.name for path in systemd_dir.glob("joplin-md-sync*.service"))
    assert adapter_units == ["joplin-md-sync.service"]
    content = (systemd_dir / adapter_units[0]).read_text(encoding="utf-8")
    assert "MCP and GPT Actions service" in content
    assert "ExecStart={exec_start}" in content
    assert "UMask=0077" in content
    assert "Bearer " not in content


def test_windows_task_runs_the_same_combined_service() -> None:
    assert not (REPO / "examples" / "windows" / "install-mcp-task.ps1").exists()
    content = (REPO / "examples" / "windows" / "install-service-task.ps1").read_text(
        encoding="utf-8"
    )
    assert "[string]$GptActionsTokenFile" in content
    assert '"--gpt-actions"' in content
    assert '"--gpt-actions-token-file"' in content
    assert "if ($McpAuthTokenFile)" in content
    assert '"--auth-token-file"' in content
    assert '[string]$TaskName = "joplin-md-sync"' in content


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


def test_setup_assistant_is_shipped_without_a_checked_in_contract() -> None:
    assert (REPO / "scripts" / "prepare_chatgpt_action.py").is_file()
    assert not (REPO / "openapi" / "chatgpt-action.openapi.json").exists()
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include scripts/prepare_chatgpt_action.py" in manifest
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/chatgpt-action.openapi.json" in gitignore
