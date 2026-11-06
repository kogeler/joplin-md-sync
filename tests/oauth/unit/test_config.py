"""Config privacy, side-effect-free validation, storage ownership and recovery."""

from __future__ import annotations

import asyncio
import base64
import errno
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from key_value.aio.errors import DecryptionError

from joplin_md_sync.errors import WorkspaceLockedError
from joplin_md_sync.oauth_store import OAuthState, inspect_state
from tests.helpers import TOKEN, run_cli
from tests.oauth.helpers import OAuthArguments, protected_file

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def oauth(tmp_path):
    return OAuthArguments(tmp_path)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("allowed_github_user_id", ""),
        ("allowed_github_user_id", "*"),
        ("allowed_github_user_id", True),
        ("allowed_github_user_id", "\uff10\uff14\uff12"),
        ("allowed_github_user_id", "042"),
        ("allowed_github_user_id", ["42"]),
        ("public_base_url", "http://example.invalid"),
        ("public_base_url", "https://@example.invalid"),
        ("public_base_url", "https://example.invalid?"),
        ("public_base_url", "https://example.invalid/path"),
        ("public_base_url", "https://example.invalid/#x"),
        ("access_token_ttl_seconds", True),
        ("access_token_ttl_seconds", 599),
        ("access_token_ttl_seconds", 3601),
        ("unexpected_key", "fixture"),
    ],
)
def test_invalid_config_fails_without_provisioning(oauth, setting, value):
    oauth.values[setting] = value
    with pytest.raises(ValueError):
        oauth.config()
    assert not (oauth.directory / "state").exists()


def test_config_does_not_repr_secrets_and_normalizes_origin(oauth):
    config = oauth.config()
    assert config.allowed_github_user_id == "42"
    assert config.client_secret not in repr(config)
    assert config.signing_key.hex() not in repr(config)
    assert not config.state_dir.exists()
    oauth.values["public_base_url"] = "https://EXAMPLE.invalid:443"
    assert oauth.config().public_base_url == "https://example.invalid"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission boundary; Windows checks ACLs")
def test_unsafe_secret_permissions_are_not_silently_repaired(oauth):
    secret = oauth.secret_path
    secret.chmod(0o644)
    with pytest.raises(ValueError):
        oauth.config()
    assert secret.stat().st_mode & 0o777 == 0o644


def test_encrypted_state_restart_exclusive_lock_and_reset(oauth):
    config = oauth.config()
    assert inspect_state(config.state_dir) is False
    canary = "fixture-private-access-and-refresh-material"

    async def put(store):
        await store.put("fixture-grant-key", {"credential": canary}, collection="test", ttl=600)

    with OAuthState(config) as state:
        asyncio.run(put(state.store))
        with pytest.raises(WorkspaceLockedError):
            with OAuthState(config):
                pass
        assert inspect_state(config.state_dir) is True
    for file in config.state_dir.rglob("*"):
        if file.is_file():
            assert canary.encode() not in file.read_bytes()
            assert "fixture-grant-key" not in file.name
    with OAuthState(config) as state:
        assert asyncio.run(state.store.get("fixture-grant-key", collection="test")) == {
            "credential": canary
        }
        state.reset()
    with OAuthState(config) as state:
        assert asyncio.run(state.store.get("fixture-grant-key", collection="test")) is None
        state.reset()


def test_corruption_and_wrong_key_preserve_state(oauth):
    config = oauth.config()
    with OAuthState(config) as state:
        asyncio.run(state.store.put("fixture", {"secret": "canary"}, ttl=600))
    snapshot = {
        str(file.relative_to(config.state_dir)): file.read_bytes()
        for file in config.state_dir.rglob("*")
        if file.is_file()
    }
    with OAuthState(replace(config, signing_key=os.urandom(32))) as state:
        with pytest.raises(DecryptionError):
            asyncio.run(state.store.get("fixture"))
    assert all(
        (config.state_dir / name).read_bytes() == content for name, content in snapshot.items()
    )
    protected_file(config.state_dir / "schema.json", "invalid-schema")
    with pytest.raises(ValueError, match="schema"):
        inspect_state(config.state_dir)
    with pytest.raises(ValueError, match="schema"):
        with OAuthState(config):
            pass


def test_standard_runtime_exposes_oauth_and_checks_config_without_provisioning(oauth):
    # Exercise the same installed runtime in a separate interpreter.
    launcher = "import sys;sys.path.insert(0,sys.argv.pop(1));from joplin_md_sync.cli import main;raise SystemExit(main(sys.argv[1:]))"

    def invoke(*args):
        return subprocess.run(
            [sys.executable, "-c", launcher, str(REPO / "src"), *args],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"JOPLIN_TOKEN", "PYTHONPATH"}
            },
        )

    capabilities = invoke("capabilities", "--json")
    assert capabilities.returncode == 0
    assert json.loads(capabilities.stdout)["features"]["mcp_github_oauth"] is True
    token_file = protected_file(oauth.directory / "joplin-token", TOKEN)
    mcp_file = protected_file(
        oauth.directory / "mcp-token",
        base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode(),
    )
    result = invoke(
        "mcp",
        "oauth-check",
        *oauth.argv(),
        "--token-file",
        str(token_file),
        "--auth-token-file",
        str(mcp_file),
        "--json",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["oauth_config_valid"] is True
    assert not (oauth.directory / "state").exists()


def test_reset_requires_explicit_confirmation(oauth):
    result = run_cli("mcp", "oauth-reset", *oauth.argv(), "--json")
    assert result.exit_code == 7
    assert not (oauth.directory / "state").exists()


@pytest.mark.parametrize(
    "path",
    [
        "/claude/mcp",
        "/auth/callback",
        "/token",
        "/healthz",
        "/api/gpt/v1/tools/x",
        "/mcp/",
        "/mcp%2F",
        "/mcp//alias",
    ],
)
def test_reserved_or_ambiguous_routes_fail_check_without_provisioning(oauth, path):
    token = protected_file(oauth.directory / "joplin-token", TOKEN)
    bearer = protected_file(
        oauth.directory / "mcp-token", base64.urlsafe_b64encode(os.urandom(32)).decode()
    )
    result = run_cli(
        "mcp",
        "oauth-check",
        *oauth.argv(),
        "--mcp-path",
        path,
        "--token-file",
        str(token),
        "--auth-token-file",
        str(bearer),
        "--json",
    )
    assert result.exit_code == 7
    assert not (oauth.directory / "state").exists()


def test_state_quota_invalid_ttl_and_disk_failure_preserve_existing_grants(oauth, monkeypatch):
    config = oauth.config()

    async def exercise(store):
        await store.put("working-grant", {"credential": "retained"}, ttl=600)
        for ttl in (0, -1, float("nan"), float("inf")):
            with pytest.raises(ValueError, match="lifetime"):
                await store.put("invalid", {}, ttl=ttl)
        with monkeypatch.context() as patch:
            patch.setattr("joplin_md_sync.oauth_store.MAX_STATE_FILES", 17)
            with pytest.raises(ValueError, match="quota"):
                await store.put("overflow", {"credential": "new"}, ttl=600)
        for failure in (errno.ENOSPC, errno.EROFS):
            original_replace = Path.replace

            def fail_replace(path, target, failure=failure, original_replace=original_replace):
                if path.is_relative_to(store.root):
                    raise OSError(failure, "fixture storage failure")
                return original_replace(path, target)

            with monkeypatch.context() as patch:
                patch.setattr(Path, "replace", fail_replace)
                with pytest.raises(OSError):
                    await store.put("new-grant", {"credential": "uncommitted"}, ttl=600)
                with pytest.raises(OSError):
                    await store.put("working-grant", {"credential": "uncommitted"}, ttl=600)
            assert await store.get("new-grant") is None
            assert await store.get("working-grant") == {"credential": "retained"}
            assert not list(store.root.rglob("*.tmp"))

    with OAuthState(config) as state:
        asyncio.run(exercise(state.store))
