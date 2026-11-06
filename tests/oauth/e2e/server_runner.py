"""Production CLI with local peers, test clock and kernel-assigned listener ports."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import httpx2

from joplin_md_sync import github_oauth


def main() -> int:
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-url", required=True)
    parser.add_argument("--clock-file")
    parser.add_argument("--listener-file")
    parser.add_argument("cli", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cli = args.cli[1:] if args.cli[:1] == ["--"] else args.cli
    original_open = urllib.request.urlopen
    original_resolve = socket.getaddrinfo

    def local_resolve(host, *positional, **keywords):
        assert host in {None, "127.0.0.1", "::1", "localhost", b"127.0.0.1"}, (
            "Unexpected external network resolution"
        )
        return original_resolve(host, *positional, **keywords)

    def lookup_open(request, *positional, **keywords):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if url.startswith("https://api.github.com/users/"):
            request = urllib.request.Request(args.github_url + "/lookup/" + url.rsplit("/", 1)[-1])
        return original_open(request, *positional, **keywords)

    socket.getaddrinfo = local_resolve
    urllib.request.urlopen = lookup_open

    async def upstream(request: httpx2.Request) -> httpx2.Response:
        routes = {github_oauth.GITHUB_TOKEN_URL: "/token", github_oauth.GITHUB_USER_URL: "/user"}
        assert str(request.url) in routes, "Unexpected external HTTP request"
        async with httpx2.AsyncClient(trust_env=False, timeout=2) as client:
            response = await client.request(
                request.method,
                args.github_url + routes[str(request.url)],
                headers=dict(request.headers),
                content=request.content,
            )
        return httpx2.Response(
            response.status_code, headers=dict(response.headers), content=response.content
        )

    def client_factory() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream))

    # Inject the external peer before the lazy production server import.
    github_oauth.github_http_client = client_factory
    if args.clock_file:
        from fastmcp.server.auth import jwt_issuer
        from fastmcp.server.auth.oauth_proxy import proxy
        from mcp.server.auth.handlers import token as token_handler

        def now():
            return time.time() + float(Path(args.clock_file).read_text())

        jwt_issuer.time = SimpleNamespace(time=now)
        proxy.time = SimpleNamespace(time=now)
        token_handler.time = SimpleNamespace(time=now)
    if args.listener_file:
        # Bind port zero in the child, rather than guessing an unused port in
        # the parent and leaving a race with other parallel fixtures. Keep the
        # production protocol, server startup, routing and executor unchanged.
        from joplin_md_sync import mcp_server, oauth_server

        listener_file = Path(args.listener_file)
        current_context = None

        def publish_port(port):
            temporary = listener_file.with_suffix(".tmp")
            temporary.write_text(str(port))
            temporary.replace(listener_file)

        original_oauth_serve = oauth_server.serve_oauth_http
        original_bind = oauth_server.bind_http_listener

        def oauth_serve(config, context, **kwargs):
            nonlocal current_context
            current_context = context
            return original_oauth_serve(config, context, **kwargs)

        def reporting_bind(address):
            # Listening sockets queue readiness probes until Uvicorn accepts them.
            listener = original_bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            current_context.server_address = ("127.0.0.1", port)
            publish_port(port)
            return listener

        class ReportingLegacyServer(mcp_server.McpHttpServer):
            def server_bind(self):
                self.server_address = ("127.0.0.1", 0)
                super().server_bind()
                publish_port(self.server_port)

        oauth_server.serve_oauth_http = oauth_serve
        oauth_server.bind_http_listener = reporting_bind
        mcp_server.McpHttpServer = ReportingLegacyServer
    from joplin_md_sync.cli import main as cli_main

    return cli_main(cli)


if __name__ == "__main__":
    raise SystemExit(main())
