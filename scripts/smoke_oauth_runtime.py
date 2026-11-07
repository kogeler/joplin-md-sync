#!/usr/bin/env python3
"""Exercise the standard artifact CLI with OAuth off/on outside the checkout.

No GitHub or Joplin account is needed. Discovery, DCR and consent exercise the
real SDK, encrypted storage, crypto metadata and templates in the artifact.

With ``--probe-python`` (plus ``--probe-zipapp``) or ``--probe-executable``,
the GitHub TLS client probe also runs inside the built runtime: the production
GitHub client completes one HTTPS request to a local server whose test CA only
the probe adds, and rejects a certificate from an untrusted CA.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORIGIN = "https://oauth-smoke.invalid"
REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tests" / "oauth" / "packaging_probe.py"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def smoke(command: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="joplin-oauth-artifact-") as temporary:
        directory = Path(temporary)

        def private(name: str, value: str) -> Path:
            path = directory / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
            return path

        # Generated fixture credentials are passed exclusively through files.
        token = private("joplin-token", secrets.token_urlsafe(32))
        bearer = secrets.token_urlsafe(32)
        auth = private("mcp-token", bearer)
        actions = private("actions-token", secrets.token_urlsafe(32))
        secret = private("github-secret", secrets.token_urlsafe(32))
        key = private("signing-key", base64.urlsafe_b64encode(os.urandom(32)).decode())
        state = directory / "state"
        oauth_args = [
            "--github-oauth",
            "--github-oauth-public-url",
            ORIGIN,
            "--github-oauth-client-id",
            "artifact-smoke-client",
            "--github-oauth-client-secret-file",
            str(secret),
            "--github-oauth-signing-key-file",
            str(key),
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-state-dir",
            str(state),
        ]
        env = {
            name: value
            for name, value in os.environ.items()
            if name
            not in {
                "PYTHONPATH",
                "JOPLIN_TOKEN",
                "JOPLIN_TOKEN_FILE",
                "JOPLIN_MCP_AUTH_TOKEN_FILE",
                "JOPLIN_GPT_ACTIONS_TOKEN_FILE",
                "JOPLIN_GITHUB_OAUTH",
            }
        }
        common = ["--token-file", str(token), "--auth-token-file", str(auth)]
        checked = subprocess.run(
            [*command, "mcp", "oauth-check", *oauth_args, *common, "--json"],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert checked.returncode == 0, "Artifact OAuth configuration check failed"
        assert json.loads(checked.stdout)["oauth_config_valid"] is True
        assert not state.exists(), "Read-only check provisioned state"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        def request(path, payload=None, *, bearer_token=None):
            headers = {
                "Host": "oauth-smoke.invalid",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            }
            if payload is not None:
                headers["Content-Type"] = "application/json"
            if bearer_token is not None:
                headers["Authorization"] = "Bearer " + bearer_token
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}" + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers=headers,
            )
            try:
                response = opener.open(req, timeout=2)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, response.headers, response.read(1024 * 1024)

        @contextlib.contextmanager
        def server(oauth):
            args = [
                *command,
                "mcp",
                "serve",
                *common,
                "--mcp-port",
                str(port),
                "--gpt-actions",
                "--gpt-actions-token-file",
                str(actions),
            ]
            if oauth:
                args.extend(oauth_args)
            with (directory / "server.log").open("ab") as log:
                process = subprocess.Popen(args, cwd=directory, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        assert process.poll() is None, "Artifact exited before readiness"
                        try:
                            if request("/healthz")[0] == 200:
                                break
                        except (OSError, urllib.error.URLError):
                            pass
                        assert time.monotonic() < deadline, "Artifact startup timed out"
                        time.sleep(0.05)
                    yield
                finally:
                    if os.name == "nt":
                        # A one-file executable has a bootloader parent and an
                        # application child. TerminateProcess on the parent
                        # alone leaves the child and listener alive on Windows.
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        raise AssertionError("Artifact did not stop cleanly") from None
                    if os.name == "posix":
                        assert process.returncode in (0, -signal.SIGTERM), (
                            "Artifact shutdown failed"
                        )

        for oauth in (False, True):
            with server(oauth):
                assert request("/mcp", {})[0] == 401
                status, _, raw = request(
                    "/mcp",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                    },
                    bearer_token=bearer,
                )
                assert status == 200 and json.loads(raw)["result"]["tools"], (
                    f"Legacy listing HTTP {status}"
                )
                assert request("/api/gpt/v1/tools/__probe__", {})[0] == 401
                status, headers, _ = request("/claude/mcp", {})
                assert status == (401 if oauth else 404)
                if not oauth:
                    assert not state.exists()
                    continue
                assert "oauth-protected-resource" in headers["WWW-Authenticate"]
                status, _, raw = request("/.well-known/oauth-protected-resource/claude/mcp")
                assert status == 200 and json.loads(raw)["resource"] == ORIGIN + "/claude/mcp"
                status, _, raw = request(
                    "/register",
                    {
                        "client_name": "Artifact <fixture>",
                        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                        "token_endpoint_auth_method": "none",
                    },
                )
                assert status == 201
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(b"x" * 64).digest())
                    .rstrip(b"=")
                    .decode()
                )
                query = urllib.parse.urlencode(
                    {
                        "client_id": json.loads(raw)["client_id"],
                        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                        "response_type": "code",
                        "scope": "read:user",
                        "state": "fixture-state",
                        "code_challenge_method": "S256",
                        "code_challenge": challenge,
                        "resource": ORIGIN + "/claude/mcp",
                    }
                )
                status, headers, _ = request("/authorize?" + query)
                assert status == 302
                consent_path = urllib.parse.urlsplit(headers["Location"])
                status, _, page = request(consent_path.path + "?" + consent_path.query)
                assert status == 200 and b"csrf_token" in page
                assert b"Artifact <fixture>" not in page
        print("OAuth off/on, legacy routes, discovery, encrypted DCR and consent: OK")


def tls_client_probe(python: Path | None, zipapp: Path | None, executable: Path | None) -> None:
    """Run the GitHub TLS client probe in the runtime that will ship."""
    if executable is not None:
        # A one-file executable cannot run arbitrary code; its probe build uses
        # the release PyInstaller options with the probe as the only entry.
        command = [str(executable), "--tls-client"]
    else:
        assert python is not None
        # Isolated mode keeps the checkout and PYTHONPATH off the import path.
        command = [str(python), "-I", str(PROBE), "--tls-client"]
        if zipapp is not None:
            command.extend(["--import-path", str(zipapp)])
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="joplin-tls-probe-") as temporary:
        completed = subprocess.run(
            command, cwd=temporary, env=env, capture_output=True, text=True, timeout=120
        )
    assert completed.returncode == 0, "GitHub TLS client probe failed:\n" + completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["tls_client"] == "ok"
    module = Path(result["module"])
    assert not module.is_relative_to(REPO / "src"), "The probe imported the checkout"
    if executable is not None:
        assert result["frozen"], "The probe did not run in the frozen runtime"
    elif zipapp is not None:
        assert str(module).startswith(str(zipapp)), "The probe did not import the zipapp"
    else:
        assert module.is_relative_to(result["prefix"]), "The probe did not import the artifact"
    print(f"GitHub TLS client trust ({result['trust']}), redirect and untrusted CA rejection: OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    probe = parser.add_mutually_exclusive_group()
    probe.add_argument("--probe-python", type=Path, help="interpreter of the built runtime")
    probe.add_argument("--probe-executable", type=Path, help="native probe build to run")
    parser.add_argument("--probe-zipapp", type=Path, help="zipapp to import in --probe-python")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide an absolute executable command after --")
    if args.probe_zipapp is not None and args.probe_python is None:
        parser.error("--probe-zipapp requires --probe-python")
    smoke(command)
    if args.probe_python is not None or args.probe_executable is not None:
        tls_client_probe(args.probe_python, args.probe_zipapp, args.probe_executable)
