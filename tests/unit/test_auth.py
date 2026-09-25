"""Bearer authentication boundary and protected token-file tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.auth import (
    MAX_BEARER_TOKEN_CHARS,
    WINDOWS_ADMINISTRATORS_SID,
    WINDOWS_OWNER_RIGHTS_SID,
    WINDOWS_SYSTEM_SID,
    BearerTokenError,
    WindowsAce,
    accepts_bearer_token,
    read_protected_bearer_token,
    validate_bearer_token,
    windows_acl_problem,
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


USER_SID = "S-1-5-21-1000-2000-3000-1001"
OTHER_SID = "S-1-5-21-1000-2000-3000-1002"
EVERYONE_SID = "S-1-1-0"
FULL_CONTROL = 0x001F01FF
READ_ATTRIBUTES_AND_SYNCHRONIZE = 0x0010_0080


def _allow(sid: str, mask: int = FULL_CONTROL, flags: int = 0) -> WindowsAce:
    return WindowsAce(ace_type=0x00, flags=flags, mask=mask, sid=sid)


PRIVATE_DACL = (
    _allow(WINDOWS_SYSTEM_SID),
    _allow(WINDOWS_ADMINISTRATORS_SID),
    _allow(WINDOWS_OWNER_RIGHTS_SID),
    _allow(USER_SID),
)


def test_windows_acl_accepts_private_files_owned_by_trusted_principals() -> None:
    for owner in (USER_SID, WINDOWS_SYSTEM_SID, WINDOWS_ADMINISTRATORS_SID):
        assert windows_acl_problem(owner, PRIVATE_DACL, USER_SID) is None
    assert windows_acl_problem(USER_SID, (), USER_SID) is None
    harmless = (
        # Inherit-only entries do not apply to the file itself.
        _allow(EVERYONE_SID, flags=0x08),
        # A deny entry never grants access.
        WindowsAce(ace_type=0x01, flags=0, mask=FULL_CONTROL, sid=EVERYONE_SID),
        # Reading attributes does not expose the secret.
        _allow(OTHER_SID, mask=READ_ATTRIBUTES_AND_SYNCHRONIZE),
    )
    assert windows_acl_problem(USER_SID, PRIVATE_DACL + harmless, USER_SID) is None


def test_windows_acl_rejects_foreign_owner_and_other_account_access() -> None:
    for owner in (OTHER_SID, EVERYONE_SID, WINDOWS_OWNER_RIGHTS_SID):
        assert windows_acl_problem(owner, PRIVATE_DACL, USER_SID) == "owner"
    assert windows_acl_problem(USER_SID, None, USER_SID) == "access"
    for sensitive in (0x1, 0x2, 0x4, 0x0004_0000, 0x0008_0000, 0x1000_0000, 0x4000_0000):
        dacl = (*PRIVATE_DACL, _allow(OTHER_SID, mask=sensitive))
        assert windows_acl_problem(USER_SID, dacl, USER_SID) == "access", hex(sensitive)
    for unevaluable in (
        WindowsAce(ace_type=0x05, flags=0, mask=0, sid=None),
        WindowsAce(ace_type=0x00, flags=0, mask=FULL_CONTROL, sid=None),
    ):
        assert windows_acl_problem(USER_SID, (*PRIVATE_DACL, unevaluable), USER_SID) == "access"


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows ACL enforcement")
def test_windows_token_file_rejects_access_for_other_accounts(tmp_path: Path) -> None:
    path = protected_file(tmp_path / "token")
    assert read_protected_bearer_token(path, label="test") == TOKEN
    subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
    with pytest.raises(BearerTokenError, match="must not be accessible by other accounts"):
        read_protected_bearer_token(path, label="test")
