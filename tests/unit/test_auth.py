"""Bearer authentication boundary and protected token-file tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.auth import (
    MAX_BEARER_TOKEN_CHARS,
    BearerTokenError,
    accepts_bearer_token,
    read_protected_bearer_token,
    validate_bearer_token,
)
from joplin_md_sync.errors import AuthError
from joplin_md_sync.gpt_actions import ActionsTokenSource
from joplin_md_sync.mcp_server import BearerTokenSource

TOKEN = "dG9rZW4tdG9rZW4tdG9rZW4tdG9rZW4tMDEyMzQ1Njc4OWFiY2RlZg"


def protected_file(path: Path, value: str = TOKEN) -> Path:
    path.write_text(value + "\n", encoding="ascii")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_bearer_comparison_rejects_malformed_and_non_ascii_without_raising() -> None:
    assert accepts_bearer_token(f"Bearer {TOKEN}", TOKEN)
    for authorization in (
        None,
        TOKEN,
        f"Basic {TOKEN}",
        f"bearer {TOKEN}",
        f"Bearer  {TOKEN}",
        f"Bearer {TOKEN} ",
        "Bearer \x80",
        "Bearer e\N{LATIN SMALL LETTER E WITH ACUTE}",
        f"Bearer {'a' * (MAX_BEARER_TOKEN_CHARS + 1)}",
    ):
        assert not accepts_bearer_token(authorization, TOKEN)


def test_both_token_sources_fail_closed_for_non_ascii_authorization(tmp_path: Path) -> None:
    token_file = protected_file(tmp_path / "token")
    actions = ActionsTokenSource(token_file)
    mcp = BearerTokenSource(token_file)
    for authorization in ("Bearer \x80", "Bearer e\N{LATIN SMALL LETTER E WITH ACUTE}"):
        assert not actions.accepts(authorization)
        assert not mcp.accepts(authorization)


def test_token_format_requires_bounded_urlsafe_base64_and_256_bits() -> None:
    assert validate_bearer_token(TOKEN, label="test") == TOKEN
    for token, message in (
        ("c2hvcnQ", "32 bytes"),
        ("a token with spaces", "URL-safe Base64"),
        ("e\N{LATIN SMALL LETTER E WITH ACUTE}" * 32, "URL-safe Base64"),
        ("a" * (MAX_BEARER_TOKEN_CHARS + 1), "at most"),
    ):
        with pytest.raises(BearerTokenError, match=message):
            validate_bearer_token(token, label="test")


def test_protected_reader_rejects_multiline_non_ascii_and_oversized_files(
    tmp_path: Path,
) -> None:
    multiline = protected_file(tmp_path / "multiline")
    multiline.write_bytes((TOKEN + "\n" + TOKEN + "\n").encode("ascii"))
    with pytest.raises(BearerTokenError, match="exactly one line"):
        read_protected_bearer_token(multiline, label="test")

    non_ascii = protected_file(tmp_path / "non-ascii")
    non_ascii.write_bytes(("\N{LATIN SMALL LETTER E WITH ACUTE}" * 40).encode("utf-8"))
    with pytest.raises(BearerTokenError, match="URL-safe Base64"):
        read_protected_bearer_token(non_ascii, label="test")

    oversized = protected_file(tmp_path / "oversized")
    oversized.write_bytes(b"a" * (MAX_BEARER_TOKEN_CHARS + 3))
    with pytest.raises(BearerTokenError, match="too large"):
        read_protected_bearer_token(oversized, label="test")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission and symlink checks")
def test_mcp_source_rejects_insecure_permissions_and_symlinks(tmp_path: Path) -> None:
    insecure = protected_file(tmp_path / "insecure")
    insecure.chmod(0o644)
    with pytest.raises(AuthError, match="group or others"):
        BearerTokenSource(insecure)

    target = protected_file(tmp_path / "target")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    with pytest.raises(AuthError, match="regular file"):
        BearerTokenSource(symlink)
