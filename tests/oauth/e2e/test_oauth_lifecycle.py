"""Persistent grants, clocks, concurrency and failure recovery via the real CLI."""

from __future__ import annotations

import base64
import json
import os
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.helpers import run_cli
from tests.oauth.e2e.test_oauth_flow import app as app
from tests.oauth.e2e.test_oauth_security import check, resource
from tests.oauth.integration.test_provider_flow import authorize, exchange

pytestmark = pytest.mark.oauth_e2e("lifecycle")


def refresh(app, client_id, token):
    return app.client.post(
        "/token",
        data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token},
    )


def test_expiry_and_downstream_refresh_with_controlled_time(app):
    app.oauth.values["access_token_ttl_seconds"] = 600
    app.start()
    client_id, tokens = app.login()
    assert tokens["expires_in"] == 600
    app.advance(601)
    assert resource(app, tokens["access_token"]).status_code == 401
    renewed = refresh(app, client_id, tokens["refresh_token"])
    assert renewed.status_code == 200
    assert resource(app, renewed.json()["access_token"]).status_code == 200
    assert refresh(app, client_id, tokens["refresh_token"]).status_code == 401


def test_transparent_refresh_racing_explicit_refresh_keeps_replay_closed(app):
    app.github.github.access_ttl = 120
    app.start()
    client_id, tokens = app.login()
    # Without a race, a valid client token transparently renews expired GitHub access.
    app.advance(121)
    assert resource(app, tokens["access_token"]).status_code == 200
    assert app.github.github.refresh_count == 1
    app.advance(121)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(resource, app, tokens["access_token"])
        b = pool.submit(refresh, app, client_id, tokens["refresh_token"])
        protected, renewed = a.result(), b.result()
    assert protected.status_code in {200, 401}
    assert renewed.status_code == 200
    assert 2 <= app.github.github.refresh_count <= 3
    assert resource(app, renewed.json()["access_token"]).status_code == 200
    app.stop()
    app.start()
    assert refresh(app, client_id, tokens["refresh_token"]).status_code == 401


def test_concurrent_client_code_and_refresh_have_one_success_across_restart(app):
    app.start()
    client_id, verifier, callback = authorize(app.client, app.github)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: exchange(app.client, client_id, verifier, callback.headers["location"]),
                range(2),
            )
        )
    assert sorted(item.status_code for item in results) == [200, 401]
    tokens = next(item.json() for item in results if item.status_code == 200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: refresh(app, client_id, tokens["refresh_token"]), range(2))
        )
    assert sorted(item.status_code for item in results) == [200, 401]
    app.stop()
    app.start()
    assert refresh(app, client_id, tokens["refresh_token"]).status_code == 401
    assert (
        exchange(app.client, client_id, verifier, callback.headers["location"]).status_code == 401
    )


def test_refresh_is_bound_to_registered_client_and_verified_owner(app):
    app.start()
    client_id, tokens = app.login()
    foreign_client, _, _ = authorize(app.client, app.github)
    assert refresh(app, foreign_client, tokens["refresh_token"]).status_code == 401
    assert resource(app, tokens["refresh_token"]).status_code == 401
    assert resource(app, tokens["access_token"]).status_code == 200
    app.stop()
    app.oauth.values["allowed_github_user_id"] = "43"
    app.start()
    assert resource(app, tokens["access_token"]).status_code == 403
    assert refresh(app, client_id, tokens["refresh_token"]).status_code in {400, 403}
    assert resource(app, tokens["access_token"]).status_code in {401, 403}
    assert not app.backend.store.notes


def test_state_is_encrypted_and_another_process_cannot_own_it(app):
    app.start()
    _, tokens = app.login()
    private = {*app.github.github.issued, *tokens.values()}
    private = {value.encode() for value in private if isinstance(value, str) and len(value) > 16}
    files = [
        file
        for file in (app.directory / "state").rglob("*")
        if file.is_file() and file.name != ".lock"
    ]
    # The Windows lock byte cannot be read while its range is locked. Its size
    # cannot contain any credential; runtime validates ownership/ACL by handle.
    assert (app.directory / "state" / ".lock").stat().st_size <= 1
    assert len(files) > 3
    assert all(not any(value in file.read_bytes() for value in private) for file in files)
    # The running child's port is busy too, so a missing state lock fails to
    # bind rather than serving on the default port from the test process.
    busy = run_cli(
        "mcp",
        "serve",
        *app.oauth.argv(),
        "--token-file",
        str(app.joplin_token_file),
        "--auth-token-file",
        str(app.mcp_token_file),
        "--mcp-port",
        str(app.port),
        "--json",
    )
    assert busy.exit_code == 5
    assert resource(app, tokens["access_token"]).status_code == 200
    assert check(app).exit_code == 0


def test_wrong_signing_key_fails_closed_and_reset_allows_reconnect(app):
    app.start()
    _, tokens = app.login()
    app.stop()
    store = app.directory / "state" / "store"
    snapshot = {
        str(file.relative_to(store)): file.read_bytes()
        for file in store.rglob("*")
        if file.is_file()
    }
    app.oauth.signing_path.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode() + "\n")
    app.start()
    assert resource(app, tokens["access_token"]).status_code == 401
    assert all((store / name).read_bytes() == value for name, value in snapshot.items())
    app.stop()
    reset = run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json")
    assert reset.exit_code == 0
    app.start()
    _, fresh = app.login()
    assert resource(app, fresh["access_token"]).status_code == 200


def test_corrupt_schema_and_unsafe_state_preserve_data_and_fail_check(app):
    app.start()
    _, _ = app.login()
    app.stop()
    schema = app.directory / "state" / "schema.json"
    schema.write_text("incompatible fixture schema")
    assert check(app).exit_code == 7
    assert schema.read_text() == "incompatible fixture schema"
    assert (schema.parent / "store").exists()


def test_oauth_off_on_preserves_legacy_and_existing_grants(app):
    app.start()
    _, tokens = app.login()
    app.stop()
    snapshot = {
        str(file): file.read_bytes()
        for file in (app.directory / "state").rglob("*")
        if file.is_file()
    }
    app.start(oauth=False)
    assert app.client.post("/claude/mcp", json={}).status_code == 404
    assert app.rpc(app.mcp_token, "ping", path="/mcp") == {}
    assert all(Path(name).read_bytes() == value for name, value in snapshot.items())
    app.stop()
    app.start()
    assert resource(app, tokens["access_token"]).status_code == 200


def test_read_only_store_rejects_new_registration_preserving_existing_access(app):
    app.start()
    _, tokens = app.login()
    root = app.directory / "state" / "store"
    directories = [root, *(item for item in root.rglob("*") if item.is_dir())]
    if os.name == "posix":
        for directory in directories:
            directory.chmod(0o500)
    else:
        subprocess.run(
            # Generic W also includes READ_CONTROL on Windows, so denying it
            # prevents read-only opens. Deny the specific directory write bits.
            ["icacls", str(root), "/deny", "*S-1-1-0:(OI)(CI)(WD,AD,DC)"],
            check=True,
            capture_output=True,
        )
    try:
        response = app.client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code >= 400
        assert resource(app, tokens["access_token"]).status_code == 200
        assert not app.backend.store.notes
    finally:
        if os.name == "posix":
            for directory in directories:
                directory.chmod(0o700)
        else:
            subprocess.run(
                ["icacls", str(root), "/remove:d", "*S-1-1-0", "/T"],
                check=True,
                capture_output=True,
            )


def test_disconnected_client_does_not_replay_started_mutation(app):
    app.start()
    _, tokens = app.login()
    folder = app.backend.store.add_folder("Cancellation fixture")
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def hold(method, path, query):
        if method == "POST" and path == "/notes":
            attempts.append(path)
            entered.set()
            assert release.wait(5)

    app.backend.set_before_request(hold)
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "joplin_create_note",
                "arguments": {"parent_id": folder, "title": "Disconnected mutation"},
            },
        }
    ).encode()
    try:
        # Connect to the production listener directly. The buffering test proxy
        # would keep its upstream connection open and hide the client abort that
        # a deployed reverse proxy forwards to the adapter.
        with socket.create_connection(("127.0.0.1", app.port), timeout=2) as client:
            headers = (
                "POST /claude/mcp HTTP/1.1\r\nHost: "
                + app.proxy.origin.removeprefix("https://")
                + "\r\nAuthorization: Bearer "
                + tokens["access_token"]
                + "\r\nAccept: application/json, text/event-stream\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload))
                + "\r\n\r\n"
            ).encode()
            client.sendall(headers + payload)
            assert entered.wait(3)
        release.set()
        deadline = time.monotonic() + 5
        while len(app.backend.store.notes) != 1:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert attempts == ["/notes"]
        assert resource(app, tokens["access_token"]).status_code == 200
    finally:
        release.set()
        app.backend.set_before_request(None)


# The inventory marks each shutdown case for its platform, so neither case can
# stand in for the other and an applicable one cannot be skipped.
@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM draining contract")
def test_sigterm_drains_started_request_and_releases_state_lock(app):
    app.start()
    _, tokens = app.login()
    folder = app.backend.store.add_folder("Graceful shutdown")
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def hold(method, path, query):
        if method == "POST" and path == "/notes":
            attempts.append(path)
            entered.set()
            assert release.wait(5)

    app.backend.set_before_request(hold)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                app.tool,
                tokens["access_token"],
                "joplin_create_note",
                {"parent_id": folder, "title": "Drained mutation"},
            )
            assert entered.wait(3)
            app.process.send_signal(signal.SIGTERM)
            release.set()
            # The mutation started before SIGTERM completes and is answered.
            assert pending.result(timeout=6)["note"]["title"] == "Drained mutation"
            # Bounded exit: graceful shutdown ends well before its 30 s ceiling.
            app.process.wait(timeout=8)
            assert app.process.returncode in {0, -signal.SIGTERM}
            app.process = None
        assert attempts == ["/notes"]
        assert len(app.backend.store.notes) == 1
        # Reset needs the exclusive state lock, so it proves the lock was released.
        assert (
            run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json").exit_code
            == 0
        )
        app.assert_private_data_not_logged()
    finally:
        release.set()
        app.backend.set_before_request(None)


@pytest.mark.skipif(os.name != "nt", reason="Windows forced termination contract")
def test_forced_termination_restarts_and_recovers_state_lock(app):
    # Windows TerminateProcess cannot drain, so this claims no draining: only a
    # bounded forced exit, then restart with the same lock, state and grants.
    app.start()
    _, tokens = app.login()
    folder = app.backend.store.add_folder("Forced termination")
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def hold(method, path, query):
        if method == "POST" and path == "/notes":
            attempts.append(path)
            entered.set()
            assert release.wait(5)

    app.backend.set_before_request(hold)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                app.client.post,
                "/claude/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "joplin_create_note",
                        "arguments": {"parent_id": folder, "title": "Interrupted mutation"},
                    },
                },
                headers={
                    "Authorization": "Bearer " + tokens["access_token"],
                    "Accept": "application/json, text/event-stream",
                },
                timeout=5,
            )
            assert entered.wait(3)
            process = app.process
            started = time.monotonic()
            # The fixture force-terminates the whole process tree on Windows.
            app.stop()
            assert process.returncode is not None
            assert time.monotonic() - started < 15
            release.set()
            # The interrupted call may fail or finish; it must only end.
            pending.exception(timeout=10)
        app.backend.set_before_request(None)
        # The forced exit left valid state; the restart takes the released lock.
        assert check(app).exit_code == 0
        app.start()
        assert resource(app, tokens["access_token"]).status_code == 200
        # Neither the forced exit nor the restart replays the started mutation.
        assert attempts == ["/notes"]
        assert len(app.backend.store.notes) <= 1
        app.stop()
        assert (
            run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json").exit_code
            == 0
        )
    finally:
        release.set()
        app.backend.set_before_request(None)


def test_disk_quota_exhaustion_preserves_existing_grants(app):
    import shutil

    app.start()
    _, tokens = app.login()
    quota = app.directory / "state" / "store" / "quota-fixture"
    quota.mkdir(mode=0o700)
    try:
        for index in range(4096):
            with (quota / str(index)).open("x"):
                pass
            (quota / str(index)).chmod(0o600)
        response = app.client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code >= 400
        assert resource(app, tokens["access_token"]).status_code == 200
    finally:
        shutil.rmtree(quota)
    assert resource(app, tokens["access_token"]).status_code == 200


def test_linked_state_and_unconfirmed_reset_do_not_touch_target(app):
    target = app.directory / "operator-owned"
    target.mkdir(mode=0o700)
    marker = target / "keep"
    marker.write_text("operator fixture")
    link = app.directory / "state-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    app.oauth.values["state_dir"] = str(link)
    assert check(app).exit_code == 7
    assert run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--json").exit_code == 7
    # Explicit confirmation must not make a linked state path deletable.
    confirmed = run_cli("mcp", "oauth-reset", *app.oauth.argv(), "--confirm-reset", "--json")
    assert confirmed.exit_code == 7
    assert "symlink" in confirmed.json["error"]
    assert marker.read_text() == "operator fixture"
    assert list(target.iterdir()) == [marker]


def test_dcr_registration_capacity_preserves_existing_owner_grant(app):
    import asyncio

    from mcp.shared.auth import OAuthClientInformationFull

    from joplin_md_sync.github_oauth import (
        CLAUDE_CALLBACK,
        MAX_PENDING_CLIENTS,
        PENDING_CLIENT_TTL,
        OwnerGitHubOAuth,
    )
    from joplin_md_sync.oauth_store import OAuthState

    app.start()
    client_id, tokens = app.login()
    app.stop()
    # Fill the pending capacity in stopped encrypted storage through the
    # provider's own registration path; the owner's login established its
    # client, which pending registrations can never displace.
    config = app.oauth.config()
    with OAuthState(config) as state:
        provider = OwnerGitHubOAuth(
            client_id=config.client_id,
            client_secret=config.client_secret,
            user_id=config.allowed_github_user_id,
            base_url=config.public_base_url,
            signing_key=config.signing_key,
            storage=state.store,
        )

        async def fill():
            for index in range(MAX_PENDING_CLIENTS):
                await provider.register_client(
                    OAuthClientInformationFull(
                        client_id="pending-fixture-" + str(index),
                        redirect_uris=[CLAUDE_CALLBACK],
                        token_endpoint_auth_method="none",
                    )
                )

        asyncio.run(fill())

    def register():
        return app.client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            },
        )

    app.start()
    # A registration flood is refused or rate limited without touching the
    # token budget, so the established client keeps refreshing.
    flood = [register().status_code for _ in range(70)]
    assert set(flood) <= {400, 429} and 429 in flood
    renewed = refresh(app, client_id, tokens["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    access = renewed.json()["access_token"]
    assert resource(app, access).status_code == 200
    # Abandoned registrations expire and release capacity across a restart,
    # without a state reset.
    app.stop()
    app.advance(PENDING_CLIENT_TTL + 1)
    app.start()
    assert register().status_code == 201
    assert resource(app, access).status_code == 200
    legacy = app.rpc(app.mcp_token, "tools/list", path="/mcp")
    assert legacy["tools"]
