"""Connection configuration resolution.

Precedence (highest wins): CLI arguments, environment variables, workspace
configuration, then the built-in default ``http://127.0.0.1:41184`` (probed
with ``/ping``), falling back to automatic discovery of ports 41184-41194.
Nothing needs to be configured when Joplin runs with its default Clipper
settings; every layer stays overridable.

The token is intentionally *not* accepted as a raw CLI value (it would leak
into the process list and shell history): use ``JOPLIN_TOKEN`` or
``--token-file PATH``. The token is never stored in the workspace.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from joplin_md_sync.api import (
    DEFAULT_PORT,
    JoplinClient,
    discover_base_url,
    is_loopback_url,
    ping_url,
)
from joplin_md_sync.errors import ApiError, AuthError, UnsafeOperationError

ENV_TOKEN = "JOPLIN_TOKEN"
ENV_BASE_URL = "JOPLIN_BASE_URL"
ENV_PORT = "JOPLIN_PORT"


@dataclass
class ConnectionSettings:
    base_url: str
    token: str
    timeout: float = 30.0


def resolve_token(token_file: str | None, env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if token_file:
        path = Path(token_file)
        if not path.is_file():
            raise AuthError(f"token file not found: {path}")
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise AuthError(f"token file is empty: {path}")
        return token
    token = (env.get(ENV_TOKEN) or "").strip()
    if not token:
        raise AuthError(
            "no Joplin token configured; set JOPLIN_TOKEN or pass --token-file PATH "
            "(Joplin: Tools > Options > Web Clipper > Advanced options)"
        )
    return token


def resolve_base_url(
    *,
    cli_base_url: str | None = None,
    cli_port: int | None = None,
    workspace_base_url: str | None = None,
    allow_remote: bool = False,
    env: Mapping[str, str] | None = None,
    discover: bool = True,
    discovery_timeout: float = 2.0,
) -> str:
    env = os.environ if env is None else env
    base_url: str | None = None
    if cli_base_url:
        base_url = cli_base_url
    elif cli_port:
        base_url = f"http://127.0.0.1:{cli_port}"
    elif env.get(ENV_BASE_URL):
        base_url = env[ENV_BASE_URL]
    elif env.get(ENV_PORT):
        port_raw = env[ENV_PORT]
        try:
            base_url = f"http://127.0.0.1:{int(port_raw)}"
        except ValueError:
            raise ApiError(f"invalid {ENV_PORT} value: {port_raw!r}") from None
    elif workspace_base_url:
        base_url = workspace_base_url
    elif discover:
        # Built-in default first: Joplin's standard Clipper endpoint. Only
        # when it does not answer, probe the documented 41184-41194 range.
        default_url = f"http://127.0.0.1:{DEFAULT_PORT}"
        base_url = (
            default_url
            if ping_url(default_url, timeout=discovery_timeout)
            else discover_base_url(timeout=discovery_timeout)
        )
    else:
        base_url = f"http://127.0.0.1:{DEFAULT_PORT}"

    base_url = base_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ApiError(f"invalid Joplin base URL: {base_url!r} (must start with http:// or https://)")
    if not is_loopback_url(base_url) and not allow_remote:
        raise UnsafeOperationError(
            f"refusing non-loopback Joplin API address {base_url}; "
            "pass --allow-remote-api to override"
        )
    return base_url


def build_client(
    *,
    cli_base_url: str | None = None,
    cli_port: int | None = None,
    token_file: str | None = None,
    workspace_base_url: str | None = None,
    allow_remote: bool = False,
    timeout: float = 30.0,
    discovery_timeout: float = 2.0,
) -> JoplinClient:
    token = resolve_token(token_file)
    base_url = resolve_base_url(
        cli_base_url=cli_base_url,
        cli_port=cli_port,
        workspace_base_url=workspace_base_url,
        allow_remote=allow_remote,
        discovery_timeout=discovery_timeout,
    )
    return JoplinClient(base_url, token, timeout=timeout)
