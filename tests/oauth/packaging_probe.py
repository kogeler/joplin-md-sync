"""Executable dependency/crypto/metadata/resource probe for the E0 gate.

This harness is packaged independently of the unfinished product CLI.
All credentials below are local test fixtures.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib.metadata import version
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy.ui import create_consent_html
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from joplin_md_sync.github_oauth import CLAUDE_CALLBACK, OwnerGitHubOAuth
from joplin_md_sync.oauth_http import BoundedH11Protocol


async def probe() -> None:
    with tempfile.TemporaryDirectory(prefix="jms-oauth-probe-") as directory:

        def store() -> FernetEncryptionWrapper:
            return FernetEncryptionWrapper(
                FileTreeStore(data_directory=Path(directory)),
                source_material="packaging-fixture-key-with-32-bytes",
                salt="jms-oauth-packaging-probe",
                raise_on_decryption_error=True,
            )

        first = store()
        canary = "packaging-fixture-token-plaintext-canary"
        await first.put(key="probe", value={"token": canary}, ttl=60)
        assert await store().get(key="probe") == {"token": canary}
        for path in Path(directory).rglob("*"):
            if path.is_file():
                assert canary.encode() not in path.read_bytes()

        auth = OwnerGitHubOAuth(
            client_id="packaging-fixture-client",
            client_secret="packaging-fixture-secret",
            user_id="42",
            base_url="https://joplin.example.invalid",
            signing_key=b"packaging-fixture-key-with-32-bytes",
            storage=first,
        )
        app = FastMCP("Packaging fixture", auth=auth).http_app(
            path="/claude/mcp", stateless_http=True, json_response=True
        )
        paths = {getattr(route, "path", "") for route in app.routes}
        assert {"/authorize", "/token", "/register", "/auth/callback", "/claude/mcp"} <= paths
        consent = create_consent_html(
            client_id="probe",
            redirect_uri=CLAUDE_CALLBACK,
            scopes=["read:user"],
            txn_id="fixture",
            csrf_token="fixture",
            client_name="<script>fixture</script>",
        )
        assert "&lt;script&gt;fixture&lt;/script&gt;" in consent
        assert "Content-Security-Policy" in consent
        assert BoundedH11Protocol.max_connections > 0
        print(json.dumps({"ok": True, "fastmcp": version("fastmcp"), "mcp": version("mcp")}))


if __name__ == "__main__":
    asyncio.run(probe())
