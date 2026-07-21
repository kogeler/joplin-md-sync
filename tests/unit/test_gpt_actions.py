"""GPT Actions token source and runtime configuration safety."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.gpt_actions import (
    HARD_MAX_PAYLOAD_CHARS,
    ActionsConfig,
    ActionsTokenSource,
    validate_distinct_actions_token,
)
from tests.helpers import run_cli


def protected_token(path: Path, value: str = "a" * 43) -> Path:
    path.write_text(value + "\n", encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_token_file_validation_authentication_and_rotation(tmp_path: Path) -> None:
    token_file = protected_token(tmp_path / "actions-token")
    source = ActionsTokenSource(token_file)
    assert source.accepts(f"Bearer {'a' * 43}")
    assert not source.accepts(None)
    assert not source.accepts(f"Basic {'a' * 43}")
    assert not source.accepts(f"Bearer {'b' * 43}")
    protected_token(token_file, "b" * 43)
    assert not source.accepts(f"Bearer {'a' * 43}")
    assert source.accepts(f"Bearer {'b' * 43}")


def test_token_file_rejects_empty_short_nonregular_and_insecure(tmp_path: Path) -> None:
    empty = protected_token(tmp_path / "empty", "")
    with pytest.raises(ValueError, match="empty"):
        ActionsTokenSource(empty)
    short = protected_token(tmp_path / "short", "short")
    with pytest.raises(ValueError, match=r"URL-safe|32 bytes"):
        ActionsTokenSource(short)
    with pytest.raises(ValueError, match="regular"):
        ActionsTokenSource(tmp_path)
    if os.name == "posix":
        insecure = protected_token(tmp_path / "insecure")
        insecure.chmod(0o644)
        with pytest.raises(ValueError, match="group or others"):
            ActionsTokenSource(insecure)
        target = protected_token(tmp_path / "target")
        symlink = tmp_path / "symlink"
        symlink.symlink_to(target)
        with pytest.raises(ValueError, match=r"cannot be read|regular file"):
            ActionsTokenSource(symlink)


def test_token_directory_is_rejected_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_open(*args: object, **kwargs: object) -> int:
        raise AssertionError("os.open must not be called for a directory")

    monkeypatch.setattr("joplin_md_sync.auth.os.open", unexpected_open)
    with pytest.raises(ValueError, match="regular"):
        ActionsTokenSource(tmp_path)


def test_actions_secret_must_be_distinct() -> None:
    with pytest.raises(ValueError, match="Joplin"):
        validate_distinct_actions_token("a" * 43, joplin_token="a" * 43, mcp_token=None)
    with pytest.raises(ValueError, match="MCP"):
        validate_distinct_actions_token(
            "a" * 43, joplin_token="j" * 43, mcp_token="a" * 43
        )


def test_runtime_limits_are_strict() -> None:
    ActionsConfig()
    for kwargs in (
        {"max_request_bytes": 0},
        {"max_request_bytes": HARD_MAX_PAYLOAD_CHARS + 1},
        {"max_response_chars": 0},
        {"max_concurrency": 0},
        {"rate_limit_per_minute": 0},
    ):
        with pytest.raises(ValueError):
            ActionsConfig(**kwargs)


def test_cli_rejects_invalid_actions_configuration_before_listen(tmp_path: Path) -> None:
    result = run_cli("mcp", "serve", "--gpt-actions")
    assert result.exit_code == 7
    assert "--gpt-actions-token-file" in result.stdout

    token_file = protected_token(tmp_path / "actions-token")
    result = run_cli(
        "mcp",
        "serve",
        "--gpt-actions-token-file",
        str(token_file),
    )
    assert result.exit_code == 7
    assert "require --gpt-actions" in result.stdout

    result = run_cli(
        "mcp",
        "serve",
        "--gpt-actions",
        "--gpt-actions-token-file",
        str(token_file),
        env={"JOPLIN_TOKEN": "a" * 43},
    )
    assert result.exit_code == 7
    assert "differ from the Joplin token" in result.stdout

    mcp_file = protected_token(tmp_path / "mcp-token")
    result = run_cli(
        "mcp",
        "serve",
        "--gpt-actions",
        "--gpt-actions-token-file",
        str(token_file),
        "--auth-token-file",
        str(mcp_file),
        env={"JOPLIN_TOKEN": "j" * 43},
    )
    assert result.exit_code == 7
    assert "differ from the MCP token" in result.stdout

    weak_mcp = protected_token(tmp_path / "weak-mcp", "c2hvcnQ")
    result = run_cli(
        "mcp",
        "serve",
        "--gpt-actions",
        "--gpt-actions-token-file",
        str(token_file),
        "--auth-token-file",
        str(weak_mcp),
        env={"JOPLIN_TOKEN": "j" * 43},
    )
    assert result.exit_code == 7
    assert "32 bytes" in result.stdout

    reused_mcp = protected_token(tmp_path / "reused-mcp", "j" * 43)
    result = run_cli(
        "mcp",
        "serve",
        "--gpt-actions",
        "--gpt-actions-token-file",
        str(token_file),
        "--auth-token-file",
        str(reused_mcp),
        env={"JOPLIN_TOKEN": "j" * 43},
    )
    assert result.exit_code == 7
    assert "MCP token must differ from the Joplin token" in result.stdout
