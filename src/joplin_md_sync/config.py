"""Connection configuration resolution.

Precedence (highest wins): CLI arguments, environment variables, workspace
configuration, then the built-in default ``http://127.0.0.1:41184`` (probed
with ``/ping``), falling back to automatic discovery of ports 41184-41194.
Nothing needs to be configured when Joplin runs with its default Clipper
settings; every layer stays overridable.

Normal connection resolution intentionally does not accept a raw CLI token
(it would leak into the process list and shell history): use ``JOPLIN_TOKEN``
or ``--token-file PATH``, never both. The token file must be protected: a
bounded single-line regular file that only the current user can access. The
local ``mcp stdio`` process additionally accepts the compatibility ``--token``
argument under the same exactly-one-source rule. The token is never stored in
the workspace.
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
from joplin_md_sync.auth import ProtectedFileError, read_protected_token_line
from joplin_md_sync.errors import ApiError, AuthError, UnsafeOperationError

ENV_TOKEN = "JOPLIN_TOKEN"
ENV_BASE_URL = "JOPLIN_BASE_URL"
ENV_PORT = "JOPLIN_PORT"


@dataclass
class ConnectionSettings:
    base_url: str
    token: str
    timeout: float = 30.0


def _source_conflict(option: str) -> AuthError:
    return AuthError(
        f"Joplin token given both on the command line ({option}) and in "
        f"{ENV_TOKEN}; use exactly one"
    )


def _environment_token(env: Mapping[str, str]) -> str:
    """Return a non-blank ``JOPLIN_TOKEN``; blank values count as unset."""
    return (env.get(ENV_TOKEN) or "").strip()


def read_protected_joplin_token(path: Path) -> str:
    """Read the Joplin token from a protected single-line file."""
    try:
        raw = read_protected_token_line(path, label="Joplin")
    except ProtectedFileError as exc:
        raise AuthError(str(exc)) from None
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise AuthError(f"Joplin token file must contain ASCII text: {path}") from None
    if not token:
        raise AuthError(f"Joplin token file is empty: {path}")
    return token


def resolve_token(token_file: str | None, env: Mapping[str, str] | None = None) -> str:
    """Return the Joplin token from exactly one configured source.

    ``--token-file`` and a non-blank ``JOPLIN_TOKEN`` are alternatives.
    Configuring both is an error, checked before the file is read, so neither
    source silently wins. The file is read through the protected reader.
    """
    env = os.environ if env is None else env
    env_token = _environment_token(env)
    if token_file is not None:
        if env_token:
            raise _source_conflict("--token-file")
        return read_protected_joplin_token(Path(token_file))
    if not env_token:
        raise AuthError(
            "no Joplin token configured; set JOPLIN_TOKEN or pass --token-file PATH "
            "(Joplin: Tools > Options > Web Clipper > Advanced options)"
        )
    return env_token


def resolve_stdio_token(
    token: str | None,
    token_file: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the ``mcp stdio`` token, which also accepts a raw ``--token``.

    The raw argument is one more command-line alternative under the same
    exactly-one-source rule as ``resolve_token``.
    """
    env = os.environ if env is None else env
    if token is None:
        return resolve_token(token_file, env)
    if _environment_token(env):
        raise _source_conflict("--token")
    value = token.strip()
    if not value:
        raise AuthError("--token must contain a Joplin Web Clipper token")
    return value


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
        raise ApiError(
            f"invalid Joplin base URL: {base_url!r} (must start with http:// or https://)"
        )
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
