"""Bound public OAuth inputs and translate request-local provider failures."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from joplin_md_sync.github_oauth import MAX_ESTABLISHED_CLIENTS, GitHubUnavailable
from joplin_md_sync.gpt_actions import TokenBucket
from joplin_md_sync.json_safety import json_nesting_exceeds
from joplin_md_sync.mcp_server import MAX_REQUEST_BYTES
from joplin_md_sync.oauth_audit import audit
from joplin_md_sync.oauth_errors import OAuthFault, record_fault, request_faults


def quiet_oauth_libraries() -> None:
    """Keep third-party protocol logs from writing codes, tokens or tool bodies.

    Future child loggers inherit a NullHandler sink. Application audit logging
    uses the project's own namespace and remains available.
    """
    prefixes = ("fastmcp", "mcp", "httpx2", "httpcore2", "uvicorn")
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and any(
            name == prefix or name.startswith(prefix + ".") for prefix in prefixes
        ):
            logger.handlers.clear()
            logger.propagate = True
    for prefix in prefixes:
        logger = logging.getLogger(prefix)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False


# Separate budgets keep an anonymous registration flood from consuming the
# token budget that established clients need for refresh, and vice versa.
REGISTRATION_RATE_PER_MINUTE = 30
TOKEN_RATE_PER_MINUTE = 60
# Established clients refresh from their own budgets, so anonymous token
# traffic cannot stop a connected client, and one client cannot stop another.
ESTABLISHED_CLIENT_TOKEN_RATE_PER_MINUTE = 20
BROWSER_FLOW_RATE_PER_MINUTE = 60
# Client-neutral, so a future client allowlist does not change the public URL.
OAUTH_MCP_PATH = "/oauth/mcp"
BROWSER_FLOW_PATHS = frozenset({"/authorize", "/consent", "/auth/callback"})
FLOW_PATHS = BROWSER_FLOW_PATHS | {"/register", "/token"}
ClientClassifier = Callable[[str], Awaitable[bool]]


class OAuthBoundary:
    def __init__(
        self,
        app: ASGIApp,
        public_origin: str | None = None,
        client_classifier: ClientClassifier | None = None,
    ) -> None:
        self.app = app
        self.public_origin = public_origin
        self.client_classifier = client_classifier
        browser = (TokenBucket(BROWSER_FLOW_RATE_PER_MINUTE), "browser")
        self._flow_rates = {
            "/register": (TokenBucket(REGISTRATION_RATE_PER_MINUTE), "register"),
            "/token": (TokenBucket(TOKEN_RATE_PER_MINUTE), "token_anonymous"),
            **dict.fromkeys(BROWSER_FLOW_PATHS, browser),
        }
        self._client_rates: dict[str, TokenBucket] = {}

    async def _token_rate(self, form: dict[str, list[str]]) -> tuple[TokenBucket, str]:
        client_ids = form.get("client_id", [])
        client_id = client_ids[0] if len(client_ids) == 1 else ""
        if (
            not client_id
            or self.client_classifier is None
            or not await self.client_classifier(client_id)
        ):
            return self._flow_rates["/token"]
        bucket = self._client_rates.get(client_id)
        if bucket is None:
            if len(self._client_rates) >= MAX_ESTABLISHED_CLIENTS:
                del self._client_rates[next(iter(self._client_rates))]
            bucket = self._client_rates[client_id] = TokenBucket(
                ESTABLISHED_CLIENT_TOKEN_RATE_PER_MINUTE
            )
        return bucket, "token_client"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        faults: list[OAuthFault] = []
        token = request_faults.set(faults)
        started = False
        replaced = False
        request_started = asyncio.get_running_loop().time()
        flow_budget = 9.0
        path = scope["path"]
        status = 0
        authorized = any(name.lower() == b"authorization" for name, _ in scope["headers"])
        limit = (
            MAX_REQUEST_BYTES
            if path == OAUTH_MCP_PATH
            else (8192 if path == "/register" else 65536)
        )
        body = bytearray()

        async def tracked_send(event: Message) -> None:
            nonlocal status
            if event["type"] == "http.response.start":
                status = event["status"]
            await send(event)

        async def rate_limited(rate: tuple[TokenBucket, str]) -> bool:
            permitted, retry_after = rate[0].consume()
            if permitted:
                return False
            audit("rate_limited", bucket=rate[1])
            await JSONResponse(
                {"error": "temporarily_unavailable"},
                status_code=429,
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )(scope, receive, tracked_send)
            return True

        async def fault_response(fault: OAuthFault) -> None:
            headers = {"Cache-Control": "no-store"}
            if fault.status == 503:
                headers["Retry-After"] = "1"
            await JSONResponse(
                {"error": fault.error, "error_description": fault.description},
                status_code=fault.status,
                headers=headers,
            )(scope, receive, tracked_send)

        async def bounded_receive() -> Message:
            event = await receive()
            if event["type"] == "http.request":
                body.extend(event.get("body", b""))
                if len(body) > limit:
                    record_fault(413, "invalid_request", "Request body is too large")
                    raise ValueError("Request body is too large")
                if path in {OAUTH_MCP_PATH, "/register"} and not event.get("more_body", False):
                    if json_nesting_exceeds(bytes(body)):
                        record_fault(400, "invalid_request", "JSON nesting is too deep")
                        raise ValueError("JSON nesting is too deep")
            return event

        async def safe_send(event: Message) -> None:
            nonlocal started, replaced
            if replaced:
                return
            if event["type"] == "http.response.start":
                if faults:
                    replaced = True
                    await fault_response(faults[0])
                    return
                started = True
            await tracked_send(event)

        try:
            if self.public_origin is not None:
                headers = [
                    (name.lower(), value.decode("latin-1")) for name, value in scope["headers"]
                ]
                hosts = [value for name, value in headers if name == b"host"]
                origins = [value for name, value in headers if name == b"origin"]
                if (
                    len(hosts) != 1
                    or hosts[0].casefold() != urlsplit(self.public_origin).netloc.casefold()
                ):
                    await fault_response(OAuthFault(400, "invalid_request", "Invalid Host"))
                    return
                if len(origins) > 1 or (origins and origins[0] != self.public_origin):
                    await fault_response(
                        OAuthFault(403, "access_denied", "Origin is not permitted")
                    )
                    return
            token_form = scope["method"] == "POST" and path == "/token"
            # A token form is charged after its bounded read identifies the client.
            if path in FLOW_PATHS and not token_form and await rate_limited(self._flow_rates[path]):
                return
            if len(scope.get("query_string", b"")) > 8192:
                await fault_response(OAuthFault(414, "invalid_request", "Query is too large"))
                return
            if sum(name.lower() == b"authorization" for name, _ in scope["headers"]) > 1:
                await fault_response(
                    OAuthFault(401, "invalid_token", "Duplicate Authorization headers")
                )
                return
            # Token/consent parsers must agree on a single value per parameter.
            # Read these small public flow inputs once before the SDK parses them.
            if scope["method"] == "POST" and path in {"/token", "/register", "/consent"}:
                async with asyncio.timeout(2):
                    while (await bounded_receive()).get("more_body", False):
                        pass
                cached_body = bytes(body)
                if path != "/register":
                    form = parse_qs(cached_body.decode("utf-8"), keep_blank_values=True)
                    if token_form and await rate_limited(await self._token_rate(form)):
                        return
                    if path == "/token" and form.get("grant_type") == ["refresh_token"]:
                        flow_budget = 25.0
                    if any(len(values) != 1 for values in form.values()):
                        await fault_response(
                            OAuthFault(400, "invalid_request", "Duplicate form parameters")
                        )
                        return

                replayed = False

                async def replay_body() -> Message:
                    nonlocal replayed
                    if replayed:
                        return await receive()
                    replayed = True
                    return {"type": "http.request", "body": cached_body, "more_body": False}

                upstream_receive = replay_body
            else:
                upstream_receive = bounded_receive
            if path in FLOW_PATHS:
                async with asyncio.timeout_at(request_started + flow_budget):
                    await self.app(scope, upstream_receive, safe_send)
            else:
                await self.app(scope, upstream_receive, safe_send)
        except (GitHubUnavailable, TimeoutError):
            if started:
                raise
            await fault_response(
                OAuthFault(
                    503, "temporarily_unavailable", "OAuth service is temporarily unavailable"
                )
            )
        except (ValueError, UnicodeDecodeError):
            if started:
                raise
            await fault_response(
                faults[0] if faults else OAuthFault(400, "invalid_request", "Invalid OAuth input")
            )
        except Exception as exc:
            if started:
                raise
            if faults:
                await fault_response(faults[0])
            else:
                logging.getLogger("joplin_md_sync.oauth").error(
                    "OAuth request failed (%s)", type(exc).__name__
                )
                await fault_response(OAuthFault(500, "server_error", "OAuth request failed"))
        finally:
            request_faults.reset(token)
            # Anonymous challenges are normal discovery; presented tokens are not.
            if path == OAUTH_MCP_PATH and authorized and status in {401, 403}:
                audit("resource_denied", reason="forbidden" if status == 403 else "invalid_token")
