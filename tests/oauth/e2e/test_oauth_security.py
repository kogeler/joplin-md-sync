"""Public security and CLI acceptance through TLS, real CLI and real storage."""

from __future__ import annotations

import base64
import hmac
import json
import os
import socket
import subprocess

import pytest

from tests.helpers import run_cli
from tests.oauth.e2e.test_oauth_flow import app as app
from tests.oauth.helpers import child_environment
from tests.oauth.integration.test_provider_flow import authorize, begin_authorization, exchange

pytestmark = pytest.mark.oauth_e2e("security")


def resource(app, token):
    return app.client.post(
        "/claude/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        },
    )


def check(app, *extra):
    return run_cli(
        "mcp",
        "oauth-check",
        *app.oauth.argv(),
        "--token-file",
        str(app.joplin_token_file),
        "--auth-token-file",
        str(app.mcp_token_file),
        *extra,
        "--json",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("public_base_url", None),
        ("client_id", None),
        ("client_secret_file", None),
        ("signing_key_file", None),
        ("allowed_github_user_id", None),
        ("state_dir", None),
        ("public_base_url", "http://example.invalid"),
        ("public_base_url", "https://user@example.invalid"),
        ("public_base_url", "https://example.invalid/extra"),
        ("public_base_url", "https://example.invalid?x=1"),
        ("public_base_url", "https://example.invalid/#x"),
        ("client_id", "../invalid"),
        ("allowed_github_user_id", "*"),
        ("allowed_github_user_id", "042"),
        ("allowed_github_user_id", "\uff14\uff12"),
        ("allowed_github_user_id", "-1"),
        ("allowed_github_user_id", ""),
        ("allowed_github_login", "https://attacker.invalid"),
        ("state_dir", "relative"),
        ("client_secret_file", "relative"),
        ("signing_key_file", "relative"),
        ("access_token_ttl_seconds", 599),
        ("access_token_ttl_seconds", 3601),
    ],
)
def test_cli_invalid_or_missing_values_fail_before_binding(app, field, value):
    app.oauth.values[field] = value
    result = check(app)
    assert result.exit_code == 7
    assert not (app.directory / "state").exists()
    assert app.github.github.user_requests == 0


def test_cli_requires_enable_flag_and_distinct_private_credentials(app):
    # A busy port turns a regression into a bind failure instead of a server
    # on the default port; the reported reason distinguishes both outcomes.
    with socket.create_server(("127.0.0.1", 0)) as busy:
        result = run_cli(
            "mcp",
            "serve",
            *app.oauth.argv(enabled=False),
            "--mcp-port",
            str(busy.getsockname()[1]),
            "--json",
        )
    assert result.exit_code == 7
    assert "--github-oauth" in result.json["error"]
    result = run_cli(
        "mcp",
        "oauth-check",
        *app.oauth.argv(),
        "--token-file",
        str(app.joplin_token_file),
        "--json",
    )
    assert result.exit_code == 7
    app.oauth.secret_path.write_text(app.mcp_token + "\n")
    assert check(app).exit_code == 7
    assert not (app.directory / "state").exists()
    assert app.github.github.user_requests == 0


def test_private_secret_permissions_are_enforced_by_real_cli(app):
    path = app.oauth.secret_path
    if os.name == "posix":
        path.chmod(0o644)
    else:
        subprocess.run(
            ["icacls", str(path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True
        )
    try:
        assert check(app).exit_code == 7
        assert not (app.directory / "state").exists()
    finally:
        if os.name == "posix":
            path.chmod(0o600)
        else:
            subprocess.run(
                ["icacls", str(path), "/remove:g", "*S-1-1-0"], check=True, capture_output=True
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_in", 0),
        ("expires_in", True),
        ("refresh_token_expires_in", -1),
        ("refresh_token", ""),
        ("access_token", ""),
    ],
)
def test_malformed_github_token_profile_cannot_issue_client_tokens(app, field, value):
    app.github.github.token_overrides[field] = value
    app.start()
    _, _, callback = authorize(app.client, app.github)
    assert callback.status_code == 400
    assert "location" not in callback.headers
    assert not app.backend.store.notes


def test_nonexpiring_github_app_is_rejected_before_client_issuance(app):
    app.github.github.include_refresh = False
    app.start()
    _, _, callback = authorize(app.client, app.github)
    assert callback.status_code == 400
    assert "expiring token profile required" in callback.text
    assert not app.backend.store.notes


@pytest.mark.parametrize("scopes", ["read:user", "user", "read:user, gist"])
def test_normalized_scopes_and_renamed_login_keep_numeric_owner(app, scopes):
    app.github.github.scopes = scopes
    app.oauth.values["allowed_github_login"] = "old-login"
    app.start()
    _, tokens = app.login()
    app.github.github.login = "new-login"
    assert resource(app, tokens["access_token"]).status_code == 200


def test_repository_scope_and_claims_cannot_replace_verified_identity(app):
    app.github.github.scopes = "repo"
    app.start()
    _, _, callback = authorize(app.client, app.github)
    assert callback.status_code >= 400
    assert not app.backend.store.notes


@pytest.mark.parametrize(
    "status,headers", [(503, {}), (429, {}), (403, {"X-RateLimit-Remaining": "0"})]
)
def test_identity_outage_or_rate_limit_is_retryable_and_independent(app, status, headers):
    app.start()
    _, tokens = app.login()
    app.github.github.user_status = status
    app.github.github.user_headers = headers
    denied = resource(app, tokens["access_token"])
    assert denied.status_code == 503
    assert denied.headers["retry-after"] == "1"
    assert app.rpc(app.mcp_token, "ping", path="/mcp") == {}
    assert not app.backend.store.notes
    app.github.github.user_status = 200
    assert resource(app, tokens["access_token"]).status_code == 200


@pytest.mark.parametrize("endpoint", ["callback", "refresh"])
@pytest.mark.parametrize(
    "status,headers",
    [(503, {}), (429, {}), (403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "1"})],
)
def test_token_endpoint_outage_does_not_issue_or_replay_tokens(app, endpoint, status, headers):
    app.start()
    app.github.github.token_headers = headers
    if endpoint == "callback":
        app.github.github.token_status = status
        _, _, response = authorize(app.client, app.github)
    else:
        client_id, tokens = app.login()
        app.github.github.token_status = status
        response = app.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert not app.backend.store.notes
    app.github.github.token_status = 200
    if endpoint == "refresh":
        # Temporary failures must not consume the downstream refresh grant.
        recovered = app.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert recovered.status_code == 200, recovered.text
        repeated = app.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert repeated.status_code == 401
    else:
        app.login()


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "https://attacker.invalid/mcp"},
        {"iss": "https://attacker.invalid"},
        {"exp": 1},
        {"token_use": "refresh"},
    ],
)
def test_signed_wrong_claims_never_reach_tool_execution(app, changes):
    app.start()
    _, tokens = app.login()
    header, body, _ = tokens["access_token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    key = base64.urlsafe_b64decode(app.oauth.signing_path.read_text().strip())

    def sign(message):
        digest = hmac.digest(key, message.encode(), "sha256")
        return message + "." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    # The forged tokens are validly signed, so only their claims are rejected.
    assert sign(header + "." + body) == tokens["access_token"]
    payload = (
        base64.urlsafe_b64encode(json.dumps({**claims, **changes}).encode()).rstrip(b"=").decode()
    )
    before = app.github.github.user_requests
    assert resource(app, sign(header + "." + payload)).status_code == 401
    assert app.github.github.user_requests == before
    assert not app.backend.store.notes


@pytest.mark.parametrize(
    "uri",
    [
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/",
        "https://claude.ai/api/mcp/auth_callback?x=1",
        "https://attacker.invalid/callback",
        "http://localhost/callback",
    ],
)
def test_redirect_allowlist_is_exact(app, uri):
    app.start()
    response = app.client.post(
        "/register", json={"redirect_uris": [uri], "token_endpoint_auth_method": "none"}
    )
    assert response.status_code == 400
    assert not app.github.github.codes
    assert not app.backend.store.notes


@pytest.mark.parametrize("tampering", ["missing-csrf", "wrong-csrf", "missing-cookie"])
def test_consent_requires_csrf_and_browser_binding(app, tampering):
    app.start()
    _, _, fields, _ = begin_authorization(app.client)
    fields["action"] = "approve"
    if tampering == "missing-csrf":
        fields.pop("csrf_token")
    elif tampering == "wrong-csrf":
        fields["csrf_token"] = "invalid"
    else:
        app.client.cookies.clear()
    response = app.client.post("/consent", data=fields)
    assert response.status_code in {400, 403}
    assert not app.github.github.codes
    assert not app.backend.store.notes


def test_dcr_metadata_is_escaped_and_never_fetched(app):
    app.start()
    # Client-supplied URLs point at a reachable local listener, so any server
    # side fetch would be observed as a connection instead of a DNS failure.
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.setblocking(False)
        origin = f"https://127.0.0.1:{listener.getsockname()[1]}"
        _, _, fields, page = begin_authorization(
            app.client,
            name="<script>fixture-xss</script>",
            metadata={"logo_uri": origin + "/logo.png", "jwks_uri": origin + "/keys"},
        )
        assert "<script>fixture-xss</script>" not in page.text
        assert "&lt;script&gt;fixture-xss" in page.text
        assert app.github.github.user_requests == 0
        fields["action"] = "deny"
        response = app.client.post("/consent", data=fields)
        assert response.status_code in {302, 303}
        assert "error=" in response.headers["location"]
        assert not app.github.github.codes
        with pytest.raises(BlockingIOError):
            listener.accept()


def test_forwarding_path_aliases_duplicate_form_and_json_limits(app):
    app.start()
    metadata = app.client.get(
        "/.well-known/oauth-authorization-server",
        headers={"X-Forwarded-Host": "attacker.invalid", "X-Forwarded-Proto": "http"},
    )
    assert metadata.status_code == 200
    assert metadata.json()["issuer"].rstrip("/") == app.proxy.origin
    for path in ("/claude/mcp/", "/claude%2Fmcp"):
        assert app.client.post(path, json={}).status_code == 400
    # Identical duplicates of an otherwise valid refresh would be accepted by
    # a last-value form parser; the boundary rejects them without consuming it.
    client_id, tokens = app.login()
    form = f"grant_type=refresh_token&client_id={client_id}&refresh_token={tokens['refresh_token']}"
    response = app.client.post(
        "/token",
        content=form + "&grant_type=refresh_token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert app.github.github.refresh_count == 0
    assert (
        app.client.post(
            "/token", content=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
        ).status_code
        == 200
    )
    # An otherwise valid registration fails only because of its nesting depth.
    registration = {
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "token_endpoint_auth_method": "none",
    }
    deep = json.dumps(registration)[:-1] + ', "x": ' + '{"x":' * 129 + "0" + "}" * 129 + "}"
    assert (
        app.client.post(
            "/register", content=deep, headers={"Content-Type": "application/json"}
        ).status_code
        == 400
    )
    assert app.client.get("/healthz").status_code == 404
    assert app.client.get("/readyz").status_code == 404
    assert not app.backend.store.notes


def test_flow_rate_limit_is_bounded_without_github_or_notes(app):
    app.start()
    responses = [
        app.client.post("/token", data={"grant_type": "unsupported"}).status_code for _ in range(65)
    ]
    assert 429 in responses
    assert app.github.github.user_requests == 0
    assert not app.backend.store.notes


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
def test_cli_route_collisions_fail_without_binding_or_state(app, path):
    assert check(app, "--mcp-path", path).exit_code == 7
    assert not (app.directory / "state").exists()
    assert app.github.github.user_requests == 0


def test_account_lookup_uses_github_http_and_returns_only_numeric_identity(app):
    import subprocess
    import sys

    command = [
        sys.executable,
        "-m",
        "tests.oauth.e2e.server_runner",
        "--github-url",
        app.github.url,
        "--",
        "mcp",
        "github-user-id",
        "--login",
    ]
    for login, status in [("fixture-user", 0), ("missing-fixture", 7)]:
        result = subprocess.run(
            [*command, login, "--json"],
            env=child_environment(),
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert result.returncode == status
        if status == 0:
            import json

            assert json.loads(result.stdout)["github_user_id"] == "42"
    assert app.github.lookups == 2
    assert not (app.directory / "state").exists()


@pytest.mark.parametrize(
    "violation", ["expired-code", "wrong-client", "missing-pkce", "plain-pkce", "changed-callback"]
)
def test_authorization_code_binding_expiry_and_pkce_downgrade(app, violation):
    import base64
    import hashlib

    from joplin_md_sync.github_oauth import CLAUDE_CALLBACK

    app.start()
    client_id, verifier, callback = authorize(app.client, app.github)
    assert callback.status_code == 302
    if violation == "expired-code":
        app.advance(601)
        response = exchange(app.client, client_id, verifier, callback.headers["location"])
    elif violation == "wrong-client":
        other, _, _, _ = begin_authorization(app.client)
        response = exchange(app.client, other, verifier, callback.headers["location"])
    else:
        params = {
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "scope": "read:user",
            "state": "fixture-state",
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
        }
        if violation == "missing-pkce":
            params.pop("code_challenge")
        elif violation == "plain-pkce":
            params["code_challenge_method"] = "plain"
        else:
            params["redirect_uri"] += "?unexpected=1"
        response = app.client.get("/authorize", params=params)
    if response.status_code in {302, 303}:
        # OAuth errors may redirect to the already validated client callback.
        # They must never contain an authorization code or change destination.
        from urllib.parse import parse_qs, urlsplit

        assert violation in {"missing-pkce", "plain-pkce"}
        target = urlsplit(response.headers["location"])
        assert target.scheme + "://" + target.netloc + target.path == CLAUDE_CALLBACK
        error = parse_qs(target.query)
        assert error.get("error") == ["invalid_request"]
        assert "code" not in error
    else:
        assert response.status_code in {400, 401}
        assert "location" not in response.headers
    assert not app.backend.store.notes


def test_refresh_scope_escalation_is_rejected_without_consuming_grant(app):
    app.start()
    client_id, tokens = app.login()
    response = app.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
            "scope": "repo",
        },
    )
    assert response.status_code in {400, 401}
    assert response.json()["error"] == "invalid_scope"
    assert app.github.github.refresh_count == 0
    assert resource(app, tokens["access_token"]).status_code == 200
    renewed = app.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert renewed.status_code == 200, renewed.text
    assert app.github.github.refresh_count == 1


def test_sync_workspace_state_is_never_used_for_oauth(app):
    # Case folding and trailing-dot aliases matter on Windows; reject them
    # consistently before any provisioning on every supported platform.
    for name in (".joplin-sync", ".JOPLIN-SYNC", ".joplin-sync."):
        target = app.directory / name
        app.oauth.values["state_dir"] = str(target / "oauth")
        assert check(app).exit_code == 7
        assert not target.exists()
