"""Only a verified GitHub numeric ID grants access, independent of claims/login."""

from __future__ import annotations

import asyncio
import io
import json

import httpx2
import pytest
from fastmcp.server.auth.oauth_proxy.upstream import OAuthError

from joplin_md_sync.github_oauth import (
    GITHUB_TOKEN_URL,
    GitHubIdentityVerifier,
    GitHubTokenClient,
    GitHubUnavailable,
)
from joplin_md_sync.oauth_config import lookup_github_user_id
from joplin_md_sync.oauth_errors import OAuthFault, request_faults


@pytest.mark.parametrize(
    "identity,scopes,allowed",
    [
        (42, "read:user", True),
        (42, "user", True),
        (42, "read:user, gist", True),
        (43, "user", False),
        (42, "repo", False),
        (42, "offline_access", False),
    ],
)
def test_identity_uses_verified_id_and_normalized_api_scopes(identity, scopes, allowed):
    def respond(request):
        assert str(request.url) == "https://api.github.com/user"
        return httpx2.Response(
            200,
            json={
                "id": identity,
                "login": "renamed-account",
                "sub": "42",
                "email": "owner@example.invalid",
            },
            headers={"X-OAuth-Scopes": scopes},
        )

    verifier = GitHubIdentityVerifier(
        "42", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    )
    result = asyncio.run(verifier.verify_token("fixture-opaque-token"))
    assert (result is not None) is allowed
    if allowed:
        assert result.subject == "42"


@pytest.mark.parametrize("identity", [True, "42", None, 0, -1])
def test_malformed_identity_never_authenticates(identity):
    verifier = GitHubIdentityVerifier(
        "42",
        lambda: httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json={"id": identity})
            )
        ),
    )
    with pytest.raises(GitHubUnavailable):
        asyncio.run(verifier.verify_token("fixture"))


@pytest.mark.parametrize(
    "status,headers",
    [(503, {}), (429, {}), (403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "1"})],
)
def test_transient_identity_failures_are_distinct_from_revocation(status, headers):
    verifier = GitHubIdentityVerifier(
        "42",
        lambda: httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(status, headers=headers))
        ),
    )
    with pytest.raises(GitHubUnavailable):
        asyncio.run(verifier.verify_token("fixture"))


def test_github_timeout_and_setup_lookup_failure_are_safe(monkeypatch):
    def timeout(request):
        raise httpx2.ReadTimeout("upstream details must not escape", request=request)

    verifier = GitHubIdentityVerifier(
        "42", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(timeout))
    )
    with pytest.raises(GitHubUnavailable, match="temporarily unavailable") as result:
        asyncio.run(verifier.verify_token("fixture"))
    assert "upstream details" not in str(result.value)
    calls = []

    def lookup(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(json.dumps({"id": 42, "login": "renamed"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", lookup)
    assert lookup_github_user_id("fixture-user") == "42"
    assert calls == [("https://api.github.com/users/fixture-user", 5)]
    with pytest.raises(ValueError):
        lookup_github_user_id("https://attacker.invalid/")
    assert len(calls) == 1

    def unavailable(*args, **kwargs):
        raise OSError("unsafe upstream response")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    with pytest.raises(ValueError, match="supply a verified numeric ID"):
        lookup_github_user_id("fixture-user")


@pytest.mark.parametrize(
    "error,status,expected",
    [
        ("incorrect_client_credentials", 500, "server_error"),
        ("redirect_uri_mismatch", 500, "server_error"),
        ("bad_refresh_token", 401, "invalid_grant"),
        ("unsupported_grant_type", 400, "invalid_grant"),
    ],
)
def test_token_errors_distinguish_app_configuration_from_grants(error, status, expected):
    # GitHub reports token endpoint errors with HTTP 200 JSON bodies.
    def respond(request):
        assert str(request.url) == GITHUB_TOKEN_URL
        return httpx2.Response(200, json={"error": error})

    verifier = GitHubIdentityVerifier(
        "42", lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    )
    client = GitHubTokenClient("fixture-client", "fixture-secret", verifier)
    faults: list[OAuthFault] = []
    token = request_faults.set(faults)
    try:
        with pytest.raises(OAuthError) as raised:
            asyncio.run(client.refresh_token(GITHUB_TOKEN_URL, refresh_token="fixture"))
    finally:
        request_faults.reset(token)
    assert raised.value.error == expected
    assert [(fault.status, fault.error) for fault in faults] == [(status, expected)]
