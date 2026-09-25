"""Single-source Joplin token resolution for the local ``mcp stdio`` transport."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import auth, config
from joplin_md_sync.auth import MAX_TOKEN_FILE_BYTES
from joplin_md_sync.config import read_protected_joplin_token, resolve_stdio_token
from joplin_md_sync.errors import AuthError

CLI_TOKEN = "cli0" * 16
FILE_TOKEN = "file" * 16
ENV_TOKEN = "env0" * 16


def protected_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    if os.name == "posix":
        path.chmod(0o600)
    return path


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    return protected_file(tmp_path / "token", f"  {FILE_TOKEN}\n".encode("ascii"))


def test_each_single_source_supplies_the_trimmed_token(token_file: Path) -> None:
    assert resolve_stdio_token(f" {CLI_TOKEN}\n", None, env={}) == CLI_TOKEN
    assert resolve_stdio_token(None, str(token_file), env={}) == FILE_TOKEN
    assert resolve_stdio_token(None, None, env={"JOPLIN_TOKEN": f" {ENV_TOKEN} "}) == ENV_TOKEN


@pytest.mark.parametrize("option", ("--token", "--token-file"))
def test_command_line_and_environment_together_are_a_conflict(
    token_file: Path, monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    def unexpected_read(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("the token file must not be read on a source conflict")

    monkeypatch.setattr(config, "read_protected_token_line", unexpected_read)
    token, path = (CLI_TOKEN, None) if option == "--token" else (None, str(token_file))
    with pytest.raises(AuthError) as raised:
        resolve_stdio_token(token, path, env={"JOPLIN_TOKEN": ENV_TOKEN})
    message = str(raised.value)
    assert f"({option})" in message
    assert "JOPLIN_TOKEN" in message
    assert "use exactly one" in message
    for secret in (CLI_TOKEN, FILE_TOKEN, ENV_TOKEN):
        assert secret not in message


def test_unreadable_file_with_environment_is_still_a_conflict(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="use exactly one"):
        resolve_stdio_token(None, str(tmp_path / "missing"), env={"JOPLIN_TOKEN": ENV_TOKEN})


@pytest.mark.parametrize("blank", ("", "   ", "\n\t"))
def test_blank_environment_counts_as_unset(token_file: Path, blank: str) -> None:
    env = {"JOPLIN_TOKEN": blank}
    assert resolve_stdio_token(CLI_TOKEN, None, env=env) == CLI_TOKEN
    assert resolve_stdio_token(None, str(token_file), env=env) == FILE_TOKEN
    with pytest.raises(AuthError, match="no Joplin token configured"):
        resolve_stdio_token(None, None, env=env)


def test_missing_source_names_both_paths_and_blank_cli_token_is_rejected() -> None:
    with pytest.raises(AuthError) as raised:
        resolve_stdio_token(None, None, env={})
    assert "no Joplin token configured" in str(raised.value)
    assert "JOPLIN_TOKEN" in str(raised.value)
    assert "--token-file" in str(raised.value)
    with pytest.raises(AuthError, match="--token must contain"):
        resolve_stdio_token("  ", None, env={})


@pytest.mark.parametrize(
    ("content", "message"),
    (
        (b"", "is empty"),
        (b" \n", "is empty"),
        (FILE_TOKEN.encode() + b"\n" + FILE_TOKEN.encode() + b"\n", "exactly one line"),
        (("\N{LATIN SMALL LETTER E WITH ACUTE}" * 8).encode("utf-8"), "ASCII text"),
        (b"a" * (MAX_TOKEN_FILE_BYTES + 1), "too large"),
    ),
)
def test_token_file_content_is_bounded_single_line_ascii(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = protected_file(tmp_path / "token", content)
    with pytest.raises(AuthError, match=message) as raised:
        read_protected_joplin_token(path)
    assert FILE_TOKEN not in str(raised.value)


def test_missing_token_file_is_rejected_without_fallback(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="cannot be read"):
        resolve_stdio_token(None, str(tmp_path / "missing"), env={})


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission, owner, and symlink checks")
def test_token_file_must_be_private_owned_and_not_a_symlink(
    token_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for mode in (0o640, 0o604):
        token_file.chmod(mode)
        with pytest.raises(AuthError, match="group or others") as raised:
            read_protected_joplin_token(token_file)
        assert FILE_TOKEN not in str(raised.value)
    token_file.chmod(0o600)

    symlink = tmp_path / "symlink"
    symlink.symlink_to(token_file)
    with pytest.raises(AuthError, match="regular file"):
        read_protected_joplin_token(symlink)

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(AuthError, match="regular file"):
        read_protected_joplin_token(directory)

    other_user = os.geteuid() + 1
    monkeypatch.setattr(auth.os, "geteuid", lambda: other_user)
    with pytest.raises(AuthError, match="owned by the current user") as raised:
        read_protected_joplin_token(token_file)
    assert FILE_TOKEN not in str(raised.value)
