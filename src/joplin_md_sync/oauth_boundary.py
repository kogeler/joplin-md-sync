"""Bound public OAuth inputs and translate request-local provider failures."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from joplin_md_sync.github_oauth import GitHubUnavailable
from joplin_md_sync.gpt_actions import TokenBucket
from joplin_md_sync.json_safety import json_nesting_exceeds
from joplin_md_sync.mcp_server import MAX_REQUEST_BYTES
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


class OAuthBoundary:
    def __init__(self, app: ASGIApp, public_origin: str | None = None) -> None:
        self.app = app
        self.public_origin = public_origin
        self._flow_rate = TokenBucket(60)

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
        limit = (
            MAX_REQUEST_BYTES if path == "/claude/mcp" else (8192 if path == "/register" else 65536)
        )
        body = bytearray()

        async def fault_response(fault: OAuthFault) -> None:
            headers = {"Cache-Control": "no-store"}
            if fault.status == 503:
                headers["Retry-After"] = "1"
            await JSONResponse(
                {"error": fault.error, "error_description": fault.description},
                status_code=fault.status,
                headers=headers,
            )(scope, receive, send)

        async def bounded_receive() -> Message:
            event = await receive()
            if event["type"] == "http.request":
                body.extend(event.get("body", b""))
                if len(body) > limit:
                    record_fault(413, "invalid_request", "Request body is too large")
                    raise ValueError("Request body is too large")
                if path in {"/claude/mcp", "/register"} and not event.get("more_body", False):
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
            await send(event)

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
            if path in {"/authorize", "/register", "/token", "/consent", "/auth/callback"}:
                permitted, retry_after = self._flow_rate.consume()
                if not permitted:
                    await JSONResponse(
                        {"error": "temporarily_unavailable"},
                        status_code=429,
                        headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
                    )(scope, receive, send)
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
            if path in {"/authorize", "/register", "/token", "/consent", "/auth/callback"}:
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
