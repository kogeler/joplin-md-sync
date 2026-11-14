"""Compose independent legacy and OAuth ASGI interfaces on one listener."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from joplin_md_sync.gpt_actions import ACTIONS_NAMESPACE


class CombinedHttpApp:
    """Dispatch whole ASGI applications, retaining OAuth middleware/lifespan."""

    def __init__(self, legacy: ASGIApp, oauth: Starlette, *, mcp_path: str = "/mcp") -> None:
        self.legacy = legacy
        self.oauth = oauth
        self.oauth_paths = frozenset(
            path for route in oauth.routes if isinstance(path := getattr(route, "path", None), str)
        )
        reserved = self.oauth_paths | {"/healthz", "/readyz"}
        if (
            mcp_path in reserved
            or mcp_path == ACTIONS_NAMESPACE
            or mcp_path.startswith(ACTIONS_NAMESPACE + "/")
            or "%" in mcp_path
            or "//" in mcp_path
            or (mcp_path != "/" and mcp_path.endswith("/"))
        ):
            raise ValueError(
                "Legacy MCP path collides with reserved routes or has an ambiguous alias"
            )
        self.mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Explicitly drive the child, rather than assuming Mount starts it.
            await self.oauth(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        headers = scope["headers"]
        lengths = [value for name, value in headers if name.lower() == b"content-length"]
        transfers = [value for name, value in headers if name.lower() == b"transfer-encoding"]
        if len(lengths) > 1 or (lengths and transfers):
            await Response(status_code=400, headers={"Connection": "close"})(scope, receive, send)
            return
        path = scope["path"]
        raw = scope.get("raw_path", path.encode("utf-8"))
        if (
            not path.startswith("/")
            or b"%" in raw
            or "//" in path
            or (path != "/" and path.endswith("/"))
        ):
            await Response(status_code=400)(scope, receive, send)
            return
        if (
            path in {self.mcp_path, "/healthz", "/readyz"}
            or path == ACTIONS_NAMESPACE
            or path.startswith(ACTIONS_NAMESPACE + "/")
        ):
            await self.legacy(scope, receive, send)
        elif path in self.oauth_paths:
            await self.oauth(scope, receive, send)
        else:
            await Response(status_code=404)(scope, receive, send)
