"""Embedded GitHub OAuth and legacy interfaces on the existing HTTP listener."""

from __future__ import annotations

import logging
import socket
import urllib.parse
from concurrent.futures import Executor, ThreadPoolExecutor

import uvicorn
from fastmcp import FastMCP
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore

from joplin_md_sync import __version__
from joplin_md_sync.github_oauth import ClientFactory, OwnerGitHubOAuth, github_http_client
from joplin_md_sync.http_app import CombinedHttpApp
from joplin_md_sync.legacy_asgi import LegacyAsgiApp, LegacyContext
from joplin_md_sync.mcp_server import MAX_HTTP_CONNECTIONS
from joplin_md_sync.oauth_boundary import OAuthBoundary, quiet_oauth_libraries
from joplin_md_sync.oauth_config import OAuthConfig
from joplin_md_sync.oauth_execution import DaemonExecutor, ExecutionBudget
from joplin_md_sync.oauth_http import BoundedH11Protocol
from joplin_md_sync.oauth_store import OAuthState, inspect_state
from joplin_md_sync.oauth_tools import RegistryProvider

log = logging.getLogger("joplin_md_sync.mcp")


def create_http_app(
    config: OAuthConfig,
    context: LegacyContext,
    *,
    storage: AsyncKeyValue,
    executor: Executor,
    client_factory: ClientFactory = github_http_client,
) -> CombinedHttpApp:
    if not context.token_source.enabled:
        raise ValueError("GitHub OAuth requires a protected legacy MCP Bearer token")
    budget = ExecutionBudget(executor)
    auth = OwnerGitHubOAuth(
        client_id=config.client_id,
        client_secret=config.client_secret,
        user_id=config.allowed_github_user_id,
        base_url=config.public_base_url,
        signing_key=config.signing_key,
        storage=storage,
        access_ttl=config.access_token_ttl_seconds,
        client_factory=client_factory,
        login_hint=config.allowed_github_login,
    )
    mcp = FastMCP(
        "Joplin",
        version=__version__,
        auth=auth,
        providers=[RegistryProvider(context.dispatcher, budget)],
        mask_error_details=True,
        strict_input_validation=False,
        tasks=False,
    )
    mcp.provider_error_strategy = "raise"
    authority = urllib.parse.urlsplit(config.public_base_url).netloc
    oauth = mcp.http_app(
        path="/claude/mcp",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=[authority],
        allowed_origins=[config.public_base_url],
    )
    # FastMCP appends http_app(middleware=...) inside its authentication stack.
    # The boundary must cover authentication as well as endpoint execution.
    oauth.add_middleware(OAuthBoundary, public_origin=config.public_base_url)
    return CombinedHttpApp(LegacyAsgiApp(context, budget), oauth, mcp_path=context.endpoint)


def check_oauth(config: OAuthConfig, context: LegacyContext) -> dict[str, object]:
    # Memory storage and a lazy executor exercise the production route/config
    # builder without binding, provisioning, migration or GitHub network calls.
    with ThreadPoolExecutor(max_workers=1) as executor:
        app = create_http_app(config, context, storage=MemoryStore(), executor=executor)
    initialized = inspect_state(config.state_dir)
    return {
        "oauth_config_valid": True,
        "endpoint": config.public_base_url + "/claude/mcp",
        "allowed_github_user_id": config.allowed_github_user_id,
        "state_dir": str(config.state_dir),
        "provisioning_required": not initialized,
        "routes": sorted(app.oauth_paths),
    }


def bind_http_listener(address: tuple[str, int]) -> socket.socket:
    """Bind before Uvicorn starts, so failures surface as ordinary OSErrors.

    Uvicorn reports its own bind failures through the silenced library logger
    and exits the process without a structured CLI error.
    """
    family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    return socket.create_server(address, family=family, backlog=MAX_HTTP_CONNECTIONS)


def serve_oauth_http(
    config: OAuthConfig,
    context: LegacyContext,
    *,
    client_factory: ClientFactory = github_http_client,
) -> None:
    quiet_oauth_libraries()
    with OAuthState(config) as state:
        assert state.store is not None
        workers = DaemonExecutor()
        try:
            app = create_http_app(
                config,
                context,
                storage=state.store,
                executor=workers,
                client_factory=client_factory,
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    http=BoundedH11Protocol,
                    proxy_headers=False,
                    access_log=False,
                    log_config=None,
                    ws="none",
                    workers=1,
                    timeout_graceful_shutdown=30,
                )
            )
            with bind_http_listener(context.server_address) as listener:
                host, port = listener.getsockname()[:2]
                log.info(
                    "MCP server listening on http://%s:%d%s; OAuth MCP at %s/claude/mcp",
                    host,
                    port,
                    context.endpoint,
                    config.public_base_url,
                )
                try:
                    server.run(sockets=[listener])
                except SystemExit:
                    # Uvicorn exits after a failed application lifespan startup.
                    if server.started:
                        raise
                    raise OSError("MCP HTTP server failed to start") from None
        finally:
            workers.shutdown(wait=True, cancel_futures=True)
