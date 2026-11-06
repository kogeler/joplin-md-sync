"""An offline GitHub OAuth/API peer with real PKCE and rotating credentials."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlencode

import httpx2

from joplin_md_sync.github_oauth import GITHUB_TOKEN_URL, GITHUB_USER_URL


class MockGitHub:
    def __init__(self) -> None:
        self.user_id = 42
        self.codes: dict[str, dict[str, str]] = {}
        self.access: dict[str, int] = {}
        self.refresh: dict[str, int] = {}
        self.refresh_access: dict[str, str] = {}
        self.refresh_count = 0
        self.include_refresh = True
        self.user_status = 200
        self.user_requests = 0
        self.login = "fixture-user"
        self.scopes = "user"
        self.user_headers: dict[str, str] = {}
        self.access_ttl = 28800
        self.refresh_ttl = 15897600
        self.clock_offset = 0
        self.expires: dict[str, float] = {}
        self.token_overrides: dict[str, object] = {}
        self.token_status = 200
        self.token_headers: dict[str, str] = {}
        self.issued: set[str] = set()

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.request))

    def authorize(self, url: str) -> str:
        query = {key: values[0] for key, values in parse_qs(url.split("?", 1)[1]).items()}
        assert query["code_challenge_method"] == "S256"
        assert query["scope"] == "read:user"
        assert query["redirect_uri"].endswith("/auth/callback")
        code = secrets.token_urlsafe(24)
        self.issued.update((code, query["state"]))
        self.codes[code] = query
        return query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]})

    def request(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == GITHUB_USER_URL:
            self.user_requests += 1
            if self.user_status != 200:
                return httpx2.Response(self.user_status, headers=self.user_headers)
            token = request.headers["authorization"].removeprefix("Bearer ")
            if (
                token not in self.access
                or self.expires.get(token, float("inf")) <= time.time() + self.clock_offset
            ):
                return httpx2.Response(401)
            return httpx2.Response(
                200,
                json={"id": self.access[token], "login": self.login},
                headers={"X-OAuth-Scopes": self.scopes},
            )
        assert str(request.url) == GITHUB_TOKEN_URL, "Unexpected external network request"
        params = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        assert params["client_id"] == "fixture-client"
        assert params["client_secret"] == "fixture-secret"
        if self.token_status != 200:
            return httpx2.Response(self.token_status, headers=self.token_headers)
        if params["grant_type"] == "authorization_code":
            code = self.codes.pop(params["code"], None)
            if code is None:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(params["code_verifier"].encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert challenge == code["code_challenge"]
            identity = self.user_id
        else:
            assert params["grant_type"] == "refresh_token"
            assert "scope" not in params
            identity = self.refresh.pop(params["refresh_token"], None)
            if identity is None:
                return httpx2.Response(200, json={"error": "bad_refresh_token"})
            previous = self.refresh_access.pop(params["refresh_token"])
            self.access.pop(previous, None)
            self.expires.pop(previous, None)
            self.refresh_count += 1
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[access], self.refresh[refresh] = identity, identity
        self.refresh_access[refresh] = access
        self.expires[access] = time.time() + self.clock_offset + self.access_ttl
        self.issued.update((access, refresh))
        tokens = {
            "access_token": access,
            "expires_in": self.access_ttl,
            "scope": "user",
            "token_type": "bearer",
        }
        if self.include_refresh:
            tokens.update(refresh_token=refresh, refresh_token_expires_in=self.refresh_ttl)
        return httpx2.Response(200, json={**tokens, **self.token_overrides})
