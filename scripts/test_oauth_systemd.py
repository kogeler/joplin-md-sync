#!/usr/bin/env python3
"""Acceptance of the built OAuth executable inside a real systemd user sandbox.

Uses unique runtime units, private fixture credentials and an isolated install
prefix under .artifacts. No operator services, Joplin profile or notes are used.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/joplin_terminal_service"))
from joplin_terminal_installer import (  # noqa: E402
    cli,
    commands,
    common,
    managed,
    oauth,
    service,
)


def main(executable: Path) -> None:
    assert sys.platform == "linux"
    assert executable.is_file(), "Build the native executable first"
    (REPO / ".artifacts").mkdir(exist_ok=True)
    unit_name = "joplin-oauth-acceptance-" + secrets.token_hex(6) + ".service"
    backend_name = "joplin-oauth-backend-" + secrets.token_hex(6) + ".service"

    def control(*args, check=True):
        result = subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=20
        )
        if check and result.returncode:
            journal = subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "--unit",
                    unit_name,
                    "--lines=25",
                    "--no-pager",
                    "--output=cat",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            raise RuntimeError(
                "systemd acceptance command failed: "
                + args[0]
                + ": "
                + result.stderr.strip()
                + "\n"
                + journal.stdout
            )
        return result

    control("show", "--property=Version")
    with tempfile.TemporaryDirectory(prefix="oauth-systemd-", dir=REPO / ".artifacts") as temporary:
        root = Path(temporary)
        env = {
            **os.environ,
            "HOME": str(root),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
        }
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        args = cli.build_parser(env).parse_args(
            [
                "--joplin-prefix",
                str(root / "prefix"),
                "--profile-dir",
                str(root / "fixture-profile"),
                "--mcp-port",
                str(port),
            ]
        )
        paths = cli.build_paths(args, env)
        paths = replace(
            paths,
            unit_path=paths.unit_path.with_name(backend_name),
            adapter_unit_path=paths.adapter_unit_path.with_name(unit_name),
        )
        for directory in (
            paths.config_dir,
            paths.mcp_config_dir,
            paths.mcp_binary.parent,
            paths.adapter_unit_path.parent,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

        def private(path, value):
            path.write_text(value + "\n")
            path.chmod(0o600)
            return path

        private(paths.token_file, secrets.token_urlsafe(32))
        bearer = secrets.token_urlsafe(32)
        private(paths.mcp_auth_token_file, bearer)
        private(paths.gpt_actions_token_file, secrets.token_urlsafe(32))
        secret = private(root / "github-secret", secrets.token_urlsafe(32))
        signing = private(root / "signing-key", base64.urlsafe_b64encode(os.urandom(32)).decode())
        shutil.copy2(executable, paths.mcp_binary)
        paths.mcp_binary.chmod(0o700)
        oauth_args = [
            "--github-oauth",
            "--github-oauth-public-url",
            "https://systemd.example.invalid",
            "--github-oauth-client-id",
            "fixture-client",
            "--github-oauth-client-secret-file",
            str(secret),
            "--github-oauth-signing-key-file",
            str(signing),
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-state-dir",
            str(paths.oauth_state_path),
        ]
        args = cli.build_parser(env).parse_args(
            [
                "--joplin-prefix",
                str(root / "prefix"),
                "--profile-dir",
                str(root / "fixture-profile"),
                "--mcp-port",
                str(port),
                *oauth_args,
            ]
        )
        runner = commands.CommandRunner(common.SecretRedactor(), env)
        oauth.prepare_oauth_service(runner, paths, args)
        probe = root / "probe.py"
        probe.write_text(
            "from pathlib import Path\nimport errno\nroot=Path("
            + repr(str(root))
            + ")\nstate=Path("
            + repr(str(paths.oauth_state_path))
            + ")\ntry:\n (root/'must-stay-readonly').write_text('blocked')\nexcept OSError as exc:\n assert exc.errno in (errno.EROFS, errno.EACCES), exc.errno\nelse:\n raise AssertionError('ProtectSystem/ProtectHome did not make the fixture root read-only')\np=state/'sandbox-probe'\np.write_text('writable')\nassert p.read_text()=='writable'\np.unlink()\n"
        )
        template = (
            REPO / "scripts/joplin_terminal_service/systemd/joplin-md-sync.service"
        ).read_text()
        # Joplin availability is an external dependency. This acceptance proves
        # adapter startup, DCR and state under the exact production hardening.
        template = template.replace("Requires=joplin-terminal.service\n", "").replace(
            "After=joplin-terminal.service\n", ""
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, payload=None, token=None):
            headers = {
                "Host": "systemd.example.invalid",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            }
            if token:
                headers["Authorization"] = "Bearer " + token
            if payload is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}" + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers=headers,
            )
            try:
                response = opener.open(req, timeout=2)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, response.headers, response.read()

        def start(enabled):
            content = service.render_adapter_unit(
                template,
                paths=paths,
                api_port=args.api_port,
                mcp_port=port,
                github_oauth_args=oauth_args if enabled else (),
                oauth_state_path=paths.oauth_state_path if enabled else None,
            )
            if enabled:
                content = content.replace(
                    "ExecStart=",
                    "ExecStartPre="
                    + common.systemd_quote(sys.executable)
                    + " "
                    + common.systemd_quote(probe)
                    + "\nExecStart=",
                    1,
                )
            content += "\n[Service]\nUnsetEnvironment=JOPLIN_TOKEN JOPLIN_TOKEN_FILE\n"
            paths.adapter_unit_path.write_text(content)
            control("link", "--runtime", str(paths.adapter_unit_path))
            control("daemon-reload")
            control("start", unit_name)
            deadline = time.monotonic() + 15
            while True:
                try:
                    if request("/healthz")[0] == 200:
                        break
                except OSError:
                    pass
                assert time.monotonic() < deadline, "Native sandbox startup timed out"
                time.sleep(0.05)
            assert not (root / "must-stay-readonly").exists()
            assert request("/mcp", {})[0] == 401
            status, _, body = request(
                "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bearer
            )
            assert status == 200 and json.loads(body)["result"]["tools"]
            assert request("/api/gpt/v1/tools/__probe__", {})[0] == 401
            status, headers, _ = request("/claude/mcp", {})
            assert status == (401 if enabled else 404)
            if enabled:
                assert "oauth-protected-resource" in headers["WWW-Authenticate"]
                assert request("/.well-known/oauth-authorization-server")[0] == 200

        linked = False
        try:
            linked = True
            start(True)
            assert (
                request(
                    "/register",
                    {
                        "client_name": "private-registration-canary",
                        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                        "token_endpoint_auth_method": "none",
                    },
                )[0]
                == 201
            )
            control("stop", unit_name)
            assert (
                control("show", unit_name, "--property=Result").stdout.strip() == "Result=success"
            )
            snapshot = {
                str(file): file.read_bytes()
                for file in paths.oauth_state_path.rglob("*")
                if file.is_file()
            }
            assert snapshot and all(
                b"private-registration-canary" not in data for data in snapshot.values()
            )
            start(False)
            assert all(Path(name).read_bytes() == data for name, data in snapshot.items())
            control("stop", unit_name)
            start(True)
            control("stop", unit_name)
            result = runner.run(
                [paths.mcp_binary, "mcp", "oauth-reset", *oauth_args, "--confirm-reset", "--json"]
            )
            assert json.loads(result.stdout)["oauth_grants_reset"] is True
            assert paths.oauth_state_path in managed.managed_purge_paths(paths)
            # Scope service operations to this test's unique units for the actual
            # purge implementation; no operator service name can be addressed.
            managed.SERVICE_NAME, managed.ADAPTER_SERVICE_NAME = backend_name, unit_name
            custom = root / "operator-owned-custom-state"
            custom.mkdir(mode=0o700)
            private(custom / "keep", "operator fixture")
            managed.purge_installation(runner, Path("/usr/bin/systemctl"), paths, env)
            assert not paths.oauth_state_path.exists()
            assert secret.exists() and signing.exists() and (custom / "keep").exists()
            print(
                "PASS: real systemd read-only boundary, scoped encrypted state, native OAuth/legacy routes, restart, disable, reset and managed purge"
            )
        finally:
            if linked:
                control("stop", unit_name, check=False)
                control("disable", "--runtime", unit_name, check=False)
                control("reset-failed", unit_name, check=False)
                control("daemon-reload", check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    main(parser.parse_args().executable.resolve())
