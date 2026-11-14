"""GitHub identity policy on documented FastMCP OAuth extension points.

GitHub OAuth is part of the standard service runtime. GitHub tokens never serve as
credentials directly on the MCP resource; OAuthProxy owns the grant binding.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
from fastmcp.server.auth import OAuthProxy, TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client, OAuthError
from fastmcp.utilities.ui import create_secure_html_response
from key_value.aio.protocols import AsyncKeyValue
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from joplin_md_sync.oauth_audit import audit
from joplin_md_sync.oauth_errors import record_fault

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
GITHUB_CLIENT_AUTH_METHOD = "client_secret_post"
REGISTRY_COLLECTION = "joplin-registration-limit"
REGISTRY_KEY = "clients"
# An anonymous registration stays pending until its owner completes login.
# The window covers the 15-minute authorization transaction and 5-minute code.
PENDING_CLIENT_TTL = 1800
MAX_PENDING_CLIENTS = 64
MAX_ESTABLISHED_CLIENTS = 512
REGISTRY_TTL = 15897600
# The consent page shows a requester-supplied client name, so a phishing
# registration can look exactly like the owner's own connector.
CONSENT_HEADING = b"<h1>Application Access Request</h1>"
CONSENT_WARNING = (
    b'<div role="alert" style="margin:0 0 16px;padding:12px 16px;border-radius:8px;'
    b'border:1px solid #fca5a5;background:#fef2f2;color:#7f1d1d;text-align:left">'
    b"<strong>Only continue if you started this.</strong> Allow access only if you "
    b"began connecting Claude to this Joplin server yourself in the last few minutes. "
    b"The application name on this page is supplied by the requester and is not "
    b"verified. If someone sent you this link, choose Deny.</div>"
)
ClientFactory = Callable[[], httpx2.AsyncClient]


def github_http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=4.0, follow_redirects=False, trust_env=False)


class GitHubUnavailable(Exception):
    """Transient upstream failure, with no provider response or credential data."""

    def __init__(self, message: str, *, stage: str) -> None:
        record_fault(503, "temporarily_unavailable", message)
        audit("github_unavailable", stage=stage)
        super().__init__(message)


class VerificationScope:
    """Reset request caching without rewriting exceptions from the OAuth SDK.

    SDK protocol errors are frozen dataclasses. Generator context managers may
    rewrite their traceback on exit, raising FrozenInstanceError instead of the
    original protocol error. A direct context manager preserves that error.
    """

    def __init__(self, cache: ContextVar[dict[str, AccessToken] | None]) -> None:
        self.cache = cache
        self.token: Token[dict[str, AccessToken] | None] | None = None

    def __enter__(self) -> None:
        self.token = self.cache.set({})

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.token is not None
        self.cache.reset(self.token)


class GitHubIdentityVerifier(TokenVerifier):
    """Verify opaque tokens with GitHub, normalize scopes, enforce one account."""

    def __init__(self, user_id: str, client_factory: ClientFactory = github_http_client) -> None:
        super().__init__(required_scopes=["read:user"])
        if not user_id.isascii() or not user_id.isdecimal() or user_id.startswith("0"):
            raise ValueError("allowed_github_user_id must be a positive ASCII decimal ID")
        self.user_id = user_id
        self.client_factory = client_factory
        self._verification: ContextVar[dict[str, AccessToken] | None] = ContextVar(
            "github_request_verification", default=None
        )

    def request_scope(self) -> VerificationScope:
        return VerificationScope(self._verification)

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = self._verification.get()
        if verified is not None and token in verified:
            return verified[token]
        try:
            async with self.client_factory() as client:
                response = await client.get(
                    GITHUB_USER_URL,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "joplin-md-sync",
                    },
                )
        except httpx2.RequestError:
            raise GitHubUnavailable(
                "GitHub identity verification is temporarily unavailable", stage="identity"
            ) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubUnavailable(
                "GitHub identity verification is temporarily unavailable", stage="identity"
            )
        if response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        ):
            raise GitHubUnavailable(
                "GitHub identity verification is rate limited", stage="identity"
            )
        if response.status_code != 200:
            return None
        try:
            data = response.json()
            identity = data["id"]
        except (ValueError, KeyError, TypeError):
            raise GitHubUnavailable(
                "GitHub returned an invalid identity response", stage="identity"
            ) from None
        if type(identity) is not int or identity <= 0:
            raise GitHubUnavailable(
                "GitHub returned an invalid identity response", stage="identity"
            )
        if str(identity) != self.user_id:
            record_fault(403, "access_denied", "GitHub account is not permitted")
            audit("foreign_account", github_id=identity)
            return None
        scopes = {item.strip() for item in response.headers.get("x-oauth-scopes", "").split(",")}
        if not scopes.intersection({"read:user", "user"}):
            return None
        result = AccessToken(
            token=token,
            client_id=str(identity),
            subject=str(identity),
            scopes=["read:user"],
            claims={"sub": str(identity)},
        )
        if verified is not None:
            verified[token] = result
        return result


class GitHubTokenClient(AsyncOAuth2Client):
    """Narrow upstream client surface supported by OAuthProxy's factory hook.

    Normalize GitHub TTLs and reject foreign identities before the OAuth proxy
    issues a client authorization code. Refreshed grants are owner-checked by
    the proxy hooks after the rotated upstream pair is stored.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        verifier: GitHubIdentityVerifier,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.verifier = verifier

    async def aclose(self) -> None:
        pass

    async def _exchange(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if url != GITHUB_TOKEN_URL:
            raise OAuthError(error="invalid_request", description="Unexpected upstream endpoint")
        try:
            async with self.verifier.client_factory() as client:
                response = await client.post(
                    url,
                    data={
                        **{key: value for key, value in params.items() if value is not None},
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx2.RequestError:
            raise GitHubUnavailable(
                "GitHub token exchange is temporarily unavailable", stage="token"
            ) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubUnavailable(
                "GitHub token exchange is temporarily unavailable", stage="token"
            )
        if response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        ):
            raise GitHubUnavailable("GitHub token exchange is rate limited", stage="token")
        refresh = params.get("grant_type") == "refresh_token"
        denied = "refresh_denied" if refresh else "login_denied"
        try:
            tokens = response.json()
            if isinstance(tokens, dict) and tokens.get("error") in {
                "invalid_grant",
                "bad_verification_code",
                "bad_refresh_token",
            }:
                record_fault(401, "invalid_grant", "GitHub grant is invalid or expired")
                audit(denied, reason="revoked" if refresh else "invalid_code")
                raise OAuthError(
                    error="invalid_grant", description="GitHub grant is invalid or expired"
                )
            if isinstance(tokens, dict) and tokens.get("error") in {
                "incorrect_client_credentials",
                "redirect_uri_mismatch",
            }:
                # Operator configuration, not the grant: clients keep their
                # refresh tokens and the operator sees an accurate cause.
                record_fault(500, "server_error", "GitHub rejected the OAuth App configuration")
                audit(denied, reason="app_configuration")
                raise OAuthError(
                    error="server_error",
                    description="GitHub rejected the OAuth App configuration",
                )
            if response.status_code != 200 or not isinstance(tokens, dict) or "error" in tokens:
                raise ValueError
            for name in ("access_token", "refresh_token"):
                if not isinstance(tokens.get(name), str) or not tokens[name]:
                    raise ValueError
            for name in ("expires_in", "refresh_token_expires_in"):
                value = tokens.get(name)
                if type(value) is not int or value <= 0:
                    raise ValueError
        except (ValueError, TypeError):
            record_fault(400, "invalid_grant", "GitHub expiring token profile required")
            audit(denied, reason="nonexpiring_profile")
            raise OAuthError(
                error="invalid_grant", description="GitHub expiring token profile required"
            ) from None
        # A refresh keeps the grant's GitHub account. OAuthProxy stores the
        # rotated pair before `_extract_upstream_claims` (explicit refresh) or
        # per-request verification (transparent refresh) checks the owner, so
        # an identity outage after GitHub rotated the grant stays retryable.
        if not refresh and await self.verifier.verify_token(tokens["access_token"]) is None:
            raise OAuthError(error="access_denied", description="GitHub account is not permitted")
        tokens["refresh_expires_in"] = tokens["refresh_token_expires_in"]
        # The upstream wire scopes may be normalized to `user` by GitHub.
        tokens["scope"] = "read:user"
        return tokens

    async def fetch_token(
        self, url: str, *, grant_type: str = "authorization_code", **params: Any
    ) -> dict[str, Any]:
        return await self._exchange(url, {"grant_type": grant_type, **params})

    async def refresh_token(
        self, url: str, *, refresh_token: str | None = None, **params: Any
    ) -> dict[str, Any]:
        # GitHub retains the original grant scopes and does not accept scope
        # changes on refresh; OAuthProxy otherwise forwards its scope field.
        params.pop("scope", None)
        return await self._exchange(
            url, {"grant_type": "refresh_token", "refresh_token": refresh_token, **params}
        )


class OwnerGitHubOAuth(OAuthProxy):
    """Single-owner OAuth, with serialized one-time exchanges and access checks."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        user_id: str,
        base_url: str,
        signing_key: bytes,
        storage: AsyncKeyValue,
        access_ttl: int = 3600,
        client_factory: ClientFactory = github_http_client,
        login_hint: str | None = None,
    ) -> None:
        self.identity = GitHubIdentityVerifier(user_id, client_factory)
        self.github_client_id = client_id
        self.github_client_secret = client_secret
        self.registration_storage = storage
        self._exchange_lock = asyncio.Lock()
        # The process owns the state directory exclusively, so the loaded
        # registry stays authoritative; writes go to storage before memory.
        self._registry: dict[str, dict[str, Any]] | None = None
        self._registry_lock = asyncio.Lock()
        super().__init__(
            upstream_authorization_endpoint=GITHUB_AUTHORIZE_URL,
            upstream_token_endpoint=GITHUB_TOKEN_URL,
            upstream_client_id=client_id,
            upstream_client_secret=client_secret,
            token_verifier=self.identity,
            base_url=base_url,
            redirect_path="/auth/callback",
            allowed_client_redirect_uris=[CLAUDE_CALLBACK],
            valid_scopes=["read:user"],
            client_storage=storage,
            jwt_signing_key=signing_key,
            forward_pkce=True,
            forward_resource=False,
            token_endpoint_auth_method=GITHUB_CLIENT_AUTH_METHOD,
            require_authorization_consent=True,
            enable_cimd=False,
            extra_authorize_params={"login": login_hint} if login_hint else None,
            fastmcp_access_token_expiry_seconds=access_ttl,
        )

    def _create_upstream_oauth_client(self) -> GitHubTokenClient:
        return GitHubTokenClient(self.github_client_id, self.github_client_secret, self.identity)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if str(params.redirect_uri) != CLAUDE_CALLBACK:
            record_fault(400, "invalid_request", "Only the hosted Claude callback is permitted")
            raise AuthorizeError("invalid_request", "Only the hosted Claude callback is permitted")
        return await super().authorize(client, params)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        # Disable the library's convenience synthesis of the upstream App ID:
        # this interface uses registered downstream clients only.
        if client_id == self.github_client_id:
            return None
        entry = (await self._client_registry()).get(client_id)
        if entry is None or _pending_expired(entry, time.time()):
            return None
        client = await super().get_client(client_id)
        if client is not None and (
            not client.redirect_uris
            or any(str(uri) != CLAUDE_CALLBACK for uri in client.redirect_uris)
        ):
            return None
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # The SDK's redirect pattern matching tolerates extra query parameters.
        # This service permits only the exact hosted Claude callback.
        if not client_info.redirect_uris or any(
            str(uri) != CLAUDE_CALLBACK for uri in client_info.redirect_uris
        ):
            audit("registration_rejected", reason="redirect_uri")
            raise RegistrationError(
                "invalid_redirect_uri", "Only the hosted Claude callback is permitted"
            )
        client_id = client_info.client_id
        assert client_id is not None
        async with asyncio.timeout(9), self._exchange_lock:
            now = time.time()
            registry = dict(await self._client_registry())
            for expired in [key for key, entry in registry.items() if _pending_expired(entry, now)]:
                del registry[expired]
                await self._client_store.delete(key=expired)
            pending = sum(entry["state"] == "pending" for entry in registry.values())
            if client_id not in registry and pending >= MAX_PENDING_CLIENTS:
                audit("registration_rejected", reason="capacity")
                raise RegistrationError(
                    "invalid_client_metadata",
                    "Client registration capacity exceeded; retry later",
                )
            await super().register_client(client_info)
            # Until an owner login promotes it, the stored record expires with
            # the pending window instead of holding storage for months.
            stored = await self._client_store.get(key=client_id)
            if stored is not None:
                await self._client_store.put(key=client_id, value=stored, ttl=PENDING_CLIENT_TTL)
            registry[client_id] = {"state": "pending", "since": int(now)}
            await self._save_registry(registry)
        audit(
            "client_registered",
            client=client_id,
            pending=sum(entry["state"] == "pending" for entry in registry.values()),
        )

    async def is_established_client(self, client_id: str) -> bool:
        """In-memory check that an owner login or refresh established the client."""
        entry = (await self._client_registry()).get(client_id)
        return entry is not None and entry["state"] == "established"

    async def announce_started(self) -> None:
        states = [entry["state"] for entry in (await self._client_registry()).values()]
        audit(
            "service_started",
            established=states.count("established"),
            pending=states.count("pending"),
        )

    async def _client_registry(self) -> dict[str, dict[str, Any]]:
        async with self._registry_lock:
            if self._registry is None:
                saved = await self.registration_storage.get(
                    REGISTRY_KEY, collection=REGISTRY_COLLECTION
                )
                registry: dict[str, dict[str, Any]] = {}
                entries = saved.get("clients") if saved is not None else None
                if isinstance(entries, dict):
                    for key, entry in entries.items():
                        if (
                            isinstance(entry, dict)
                            and entry.get("state") in {"pending", "established"}
                            and type(entry.get("since")) is int
                        ):
                            registry[key] = {"state": entry["state"], "since": entry["since"]}
                elif saved is not None:
                    # Unreleased list format: nothing proves owner authorization,
                    # so former entries must log in or refresh within the window.
                    for key in saved.get("ids", []):
                        if isinstance(key, str):
                            registry[key] = {"state": "pending", "since": int(time.time())}
                self._registry = registry
            return self._registry

    async def _save_registry(self, registry: dict[str, dict[str, Any]]) -> None:
        await self.registration_storage.put(
            REGISTRY_KEY,
            {"clients": registry},
            collection=REGISTRY_COLLECTION,
            ttl=REGISTRY_TTL,
        )
        self._registry = registry

    async def _established_capacity(self, client_id: str) -> bool:
        registry = await self._client_registry()
        entry = registry.get(client_id)
        if entry is not None and entry["state"] == "established":
            return True
        established = [key for key, item in registry.items() if item["state"] == "established"]
        if len(established) < MAX_ESTABLISHED_CLIENTS:
            return True
        # Established clients whose stored record expired release their slot;
        # live clients and their grants are never evicted.
        live = {key for key in established if await super().get_client(key) is not None}
        if len(live) >= MAX_ESTABLISHED_CLIENTS:
            return False
        await self._save_registry(
            {
                key: item
                for key, item in registry.items()
                if item["state"] != "established" or key in live
            }
        )
        return True

    async def _establish(self, client_id: str) -> None:
        registry = await self._client_registry()
        entry = registry.get(client_id)
        if entry is None or entry["state"] == "established":
            return
        stored = await self._client_store.get(key=client_id)
        if stored is None:
            return
        # Promotion restores the normal record lifetime after owner login.
        await self._client_store.put(key=client_id, value=stored)
        await self._save_registry(
            {**registry, client_id: {"state": "established", "since": int(time.time())}}
        )

    async def _extract_upstream_claims(self, idp_tokens: dict[str, Any]) -> dict[str, Any]:
        if await self.identity.verify_token(idp_tokens["access_token"]) is None:
            raise TokenError("invalid_grant", "GitHub account is not permitted")
        return {"sub": self.identity.user_id}

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        # File-backed reads must not race deletion by a concurrent exchange.
        async with asyncio.timeout(9), self._exchange_lock:
            return await super().load_authorization_code(client, authorization_code)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with asyncio.timeout(9), self._exchange_lock:
            loaded = await super().load_refresh_token(client, refresh_token)
        if loaded is None:
            # Replayed after rotation, expired, revoked or bound to another client.
            audit("refresh_denied", reason="unknown_token")
        return loaded

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        assert client.client_id is not None
        async with asyncio.timeout(9), self._exchange_lock:
            if not await self._established_capacity(client.client_id):
                audit("login_denied", reason="capacity")
                raise TokenError("invalid_client", "Client capacity exceeded")
            new_client = not await self.is_established_client(client.client_id)
            with self.identity.request_scope():
                tokens = await super().exchange_authorization_code(client, authorization_code)
            await self._establish(client.client_id)
            audit("token_issued", client=client.client_id, new_client="yes" if new_client else "no")
            return tokens

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        assert client.client_id is not None
        async with asyncio.timeout(25), self._exchange_lock:
            with self.identity.request_scope():
                tokens = await super().exchange_refresh_token(client, refresh_token, scopes)
            # A verified refresh proves the grant came from an owner login.
            if await self._established_capacity(client.client_id):
                await self._establish(client.client_id)
            audit("refresh_succeeded", client=client.client_id)
            return tokens

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Covers transparent upstream refresh as well as ordinary token reads.
        async with asyncio.timeout(9), self._exchange_lock:
            with self.identity.request_scope():
                loaded = await super().load_access_token(token)
                if loaded is not None and not isinstance(loaded, AccessToken):
                    raise GitHubUnavailable("Incompatible OAuth token result", stage="identity")
                return loaded

    async def _show_consent_page(self, request: Request) -> HTMLResponse | RedirectResponse:
        response = await super()._show_consent_page(request)
        if isinstance(response, RedirectResponse) or response.status_code != 200:
            return response
        body = bytes(response.body)
        if body.count(CONSENT_HEADING) != 1:
            # Never show an approval page without the phishing warning.
            audit("consent_warning_missing")
            return create_secure_html_response(
                "<h1>Error</h1><p>The consent page is unavailable</p>", status_code=500
            )
        response.body = body.replace(CONSENT_HEADING, CONSENT_HEADING + CONSENT_WARNING)
        response.headers["content-length"] = str(len(response.body))
        return response

    async def _submit_consent(self, request: Request) -> HTMLResponse | RedirectResponse:
        txn_id = str((await request.form()).get("txn_id", ""))
        transaction = await self._transaction_store.get(key=txn_id) if txn_id else None
        client = transaction.client_id if transaction is not None else ""
        response = await super()._submit_consent(request)
        if isinstance(response, RedirectResponse):
            location = response.headers.get("location", "")
            approved = location.startswith(GITHUB_AUTHORIZE_URL + "?")
            audit("consent_approved" if approved else "consent_denied", client=client)
        else:
            # 403 is the double-submit cookie mismatch of a forged submission.
            reason = "session" if response.status_code == 403 else "invalid"
            audit("consent_rejected", reason=reason)
        return response

    async def _handle_idp_callback(self, request: Request) -> HTMLResponse | RedirectResponse:
        response = await super()._handle_idp_callback(request)
        if isinstance(response, RedirectResponse):
            query = parse_qs(urlsplit(response.headers.get("location", "")).query)
            if "error" in query:
                audit("callback_rejected", reason="upstream_error")
        elif response.status_code == 403:
            # The browser that approved consent did not complete the login.
            audit("callback_rejected", reason="binding")
        elif response.status_code == 400:
            audit("callback_rejected", reason="transaction")
        else:
            audit("callback_rejected", reason="upstream_error")
        return response


def _pending_expired(entry: dict[str, Any], now: float) -> bool:
    return bool(entry["state"] == "pending" and now - entry["since"] > PENDING_CLIENT_TTL)
