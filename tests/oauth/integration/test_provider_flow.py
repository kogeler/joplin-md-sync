"""Full HTTP OAuth exchanges against a mocked upstream; no static verifier."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.testclient import TestClient

from joplin_md_sync import github_oauth
from joplin_md_sync.github_oauth import CLAUDE_CALLBACK, OwnerGitHubOAuth
from joplin_md_sync.oauth_boundary import OAuthBoundary
from tests.oauth.fake_github import MockGitHub

BASE = "https://joplin.example.invalid"


def application(github: MockGitHub, storage: MemoryStore | None = None):
    auth = OwnerGitHubOAuth(
        client_id="fixture-client",
        client_secret="fixture-secret",
        user_id="42",
        base_url=BASE,
        signing_key=b"fixture-key-with-at-least-32-bytes",
        storage=storage if storage is not None else MemoryStore(),
        client_factory=github.client,
    )
    mcp = FastMCP("OAuth fixture", auth=auth)

    @mcp.tool
    def echo(text: str) -> str:
        return text

    app = mcp.http_app(
        path="/claude/mcp",
        stateless_http=True,
        json_response=True,
    )
    app.add_middleware(OAuthBoundary)
    return app


def begin_authorization(client: TestClient, *, name="Fixture", metadata=None):
    register = client.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [CLAUDE_CALLBACK],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **(metadata or {}),
        },
    )
    assert register.status_code == 201, register.text
    client_id = register.json()["client_id"]
    verifier = "fixture-pkce-verifier-" + "x" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "scope": "read:user",
            "state": "fixture-client-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": str(client.base_url).rstrip("/") + "/claude/mcp",
        },
    )
    assert response.status_code == 302, response.text
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    fields = dict(re.findall(r'name="([^\"]+)"[^>]*value="([^\"]*)"', page.text))
    assert fields.get("csrf_token")
    return client_id, verifier, fields, page


def authorize(client: TestClient, github: MockGitHub) -> tuple[str, str, httpx2.Response]:
    client_id, verifier, fields, _ = begin_authorization(client)
    fields["action"] = "approve"
    consent = client.post("/consent", data=fields)
    assert consent.status_code == 302, consent.text
    callback = github.authorize(consent.headers["location"])
    result = client.get(callback)
    return client_id, verifier, result


def exchange(client: TestClient, client_id: str, verifier: str, callback: str):
    query = parse_qs(urlsplit(callback).query)
    assert callback.startswith(CLAUDE_CALLBACK + "?")
    assert query["state"] == ["fixture-client-state"]
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": CLAUDE_CALLBACK,
            "resource": str(client.base_url).rstrip("/") + "/claude/mcp",
        },
    )


def test_owner_flow_refresh_and_replay() -> None:
    github = MockGitHub()
    with TestClient(application(github), base_url=BASE, follow_redirects=False) as client:
        denied = client.post("/claude/mcp", json={})
        assert denied.status_code == 401
        assert "oauth-protected-resource" in denied.headers["www-authenticate"]
        client_id, verifier, returned = authorize(client, github)
        assert returned.status_code == 302, returned.text
        callback = returned.headers["location"]
        response = exchange(client, client_id, verifier, callback)
        assert response.status_code == 200, response.text
        tokens = response.json()
        assert tokens["expires_in"] == 3600
        assert tokens["access_token"] not in github.access
        repeated = exchange(client, client_id, verifier, callback)
        assert repeated.status_code == 401, repeated.text
        assert repeated.json()["error"] == "invalid_grant"
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        }
        listing = client.post(
            "/claude/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["result"]["tools"][0]["name"] == "echo"
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        }
        refreshed = client.post("/token", data=payload)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
        assert github.refresh_count == 1
        replay = client.post("/token", data=payload)
        assert replay.status_code == 401
        assert replay.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("foreign,missing_refresh", [(True, False), (False, True)])
def test_callback_rejects_foreign_account_and_missing_refresh(
    foreign: bool, missing_refresh: bool
) -> None:
    github = MockGitHub()
    github.user_id = 43 if foreign else 42
    github.include_refresh = not missing_refresh
    with TestClient(application(github), base_url=BASE, follow_redirects=False) as client:
        _, _, returned = authorize(client, github)
        assert returned.status_code == (403 if foreign else 400)
        assert "location" not in returned.headers
        assert (
            "GitHub account is not permitted" in returned.text
            if foreign
            else "expiring token profile required" in returned.text
        )
        assert client.post("/claude/mcp", json={}).status_code == 401


def test_concurrent_code_and_refresh_are_single_use() -> None:
    github = MockGitHub()
    with TestClient(application(github), base_url=BASE, follow_redirects=False) as client:
        client_id, verifier, returned = authorize(client, github)
        assert returned.status_code == 302
        callback = returned.headers["location"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda _: exchange(client, client_id, verifier, callback), range(2))
            )
        assert sorted(response.status_code for response in responses) == [200, 401]
        tokens = next(response.json() for response in responses if response.status_code == 200)
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/token", data=payload), range(2)))
        assert sorted(response.status_code for response in responses) == [200, 401]
        assert github.refresh_count == 1


def test_signed_wrong_audience_issuer_type_and_expired_tokens_are_rejected():
    github = MockGitHub()
    with TestClient(application(github), base_url=BASE, follow_redirects=False) as client:
        client_id, verifier, returned = authorize(client, github)
        tokens = exchange(client, client_id, verifier, returned.headers["location"]).json()
        header, encoded, _ = tokens["access_token"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        for changes in (
            {"aud": BASE + "/mcp"},
            {"iss": "https://attacker.invalid"},
            {"exp": 1},
            {"token_use": "refresh"},
        ):
            payload = (
                base64.urlsafe_b64encode(json.dumps({**claims, **changes}).encode())
                .rstrip(b"=")
                .decode()
            )
            message = header + "." + payload
            signature = (
                base64.urlsafe_b64encode(
                    hmac.digest(b"fixture-key-with-at-least-32-bytes", message.encode(), "sha256")
                )
                .rstrip(b"=")
                .decode()
            )
            bad = message + "." + signature
            assert (
                client.post(
                    "/claude/mcp", json={}, headers={"Authorization": "Bearer " + bad}
                ).status_code
                == 401
            )
        assert (
            client.post(
                "/claude/mcp",
                json={},
                headers={"Authorization": "Bearer " + tokens["refresh_token"]},
            ).status_code
            == 401
        )
        # Existing signed subject/login claims never override GitHub's /user ID.
        github.access = dict.fromkeys(github.access, 43)
        assert (
            client.post(
                "/claude/mcp",
                json={},
                headers={"Authorization": "Bearer " + tokens["access_token"]},
            ).status_code
            == 403
        )


def registry_states(storage: MemoryStore) -> dict[str, str]:
    saved = asyncio.run(storage.get("clients", collection=github_oauth.REGISTRY_COLLECTION))
    return {key: entry["state"] for key, entry in (saved or {"clients": {}})["clients"].items()}


def registration(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id, redirect_uris=[CLAUDE_CALLBACK], token_endpoint_auth_method="none"
    )


def test_pending_registrations_expire_without_reset(monkeypatch) -> None:
    clock = [1_000_000.0]
    monkeypatch.setattr(github_oauth, "time", SimpleNamespace(time=lambda: clock[0]))
    storage = MemoryStore()
    auth = OwnerGitHubOAuth(
        client_id="fixture-client",
        client_secret="fixture-secret",
        user_id="42",
        base_url=BASE,
        signing_key=b"fixture-key-with-at-least-32-bytes",
        storage=storage,
    )

    async def scenario() -> None:
        for index in range(github_oauth.MAX_PENDING_CLIENTS):
            await auth.register_client(registration("pending-" + str(index)))
        with pytest.raises(RegistrationError):
            await auth.register_client(registration("excess"))
        assert await auth.get_client("pending-0") is not None
        clock[0] += github_oauth.PENDING_CLIENT_TTL + 1
        assert await auth.get_client("pending-0") is None
        # Expired pending entries release capacity and their stored records.
        await auth.register_client(registration("after-expiry"))
        assert await auth._client_store.get(key="pending-0") is None

    asyncio.run(scenario())
    assert registry_states(storage) == {"after-expiry": "pending"}


def test_owner_login_establishes_client_and_foreign_or_abandoned_stay_pending() -> None:
    github = MockGitHub()
    storage = MemoryStore()
    with TestClient(application(github, storage), base_url=BASE, follow_redirects=False) as client:
        abandoned, _, _, _ = begin_authorization(client)
        owner, verifier, callback = authorize(client, github)
        assert exchange(client, owner, verifier, callback.headers["location"]).status_code == 200
        github.user_id = 43
        foreign, _, returned = authorize(client, github)
        assert returned.status_code == 403
    assert registry_states(storage) == {
        abandoned: "pending",
        owner: "established",
        foreign: "pending",
    }


def test_established_cap_refuses_new_owner_clients_without_eviction(monkeypatch) -> None:
    monkeypatch.setattr(github_oauth, "MAX_ESTABLISHED_CLIENTS", 1)
    github = MockGitHub()
    storage = MemoryStore()
    with TestClient(application(github, storage), base_url=BASE, follow_redirects=False) as client:
        first, verifier, callback = authorize(client, github)
        tokens = exchange(client, first, verifier, callback.headers["location"]).json()
        second, verifier, callback = authorize(client, github)
        refused = exchange(client, second, verifier, callback.headers["location"])
        assert refused.status_code == 400
        assert refused.json()["error"] == "invalid_client"
        renewed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": first,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert renewed.status_code == 200
    assert registry_states(storage) == {first: "established", second: "pending"}


def test_unreleased_registration_list_is_read_as_pending() -> None:
    storage = MemoryStore()
    asyncio.run(
        storage.put(
            "clients", {"ids": ["legacy-client"]}, collection=github_oauth.REGISTRY_COLLECTION
        )
    )
    auth = OwnerGitHubOAuth(
        client_id="fixture-client",
        client_secret="fixture-secret",
        user_id="42",
        base_url=BASE,
        signing_key=b"fixture-key-with-at-least-32-bytes",
        storage=storage,
    )
    asyncio.run(auth.register_client(registration("new-client")))
    assert registry_states(storage) == {"legacy-client": "pending", "new-client": "pending"}
