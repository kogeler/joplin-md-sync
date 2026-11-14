"""Joplin Terminal installation: oauth."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import secrets
import stat
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .cli import github_oauth_arguments
from .commands import CommandRunner
from .common import (
    ProfileLock,
    SecretRedactor,
    ToolError,
    atomic_write,
)
from .credentials import load_secret_file, resolve_secret
from .managed import managed_oauth_state_paths, remove_managed_path
from .models import GITHUB_OAUTH_FIELDS, InstallPaths
from .prompts import SetupPrompts

LOG = logging.getLogger("joplin-terminal-installer")


def save_oauth_options(paths: InstallPaths, args: argparse.Namespace) -> None:
    if args.github_oauth:
        atomic_write(
            paths.mcp_config_dir / "oauth-settings.json",
            json.dumps(
                {
                    "schema": 1,
                    "options": {
                        field: str(value) if isinstance(value, Path) else value
                        for field in GITHUB_OAUTH_FIELDS
                        if (value := getattr(args, "github_oauth_" + field, None)) is not None
                    },
                }
            ).encode()
            + b"\n",
            0o600,
        )


def complete_oauth_options(
    args: argparse.Namespace,
    paths: InstallPaths,
    redactor: SecretRedactor,
    *,
    input_fn: Callable[[str], str],
    getpass_fn: Callable[[str], str],
) -> None:
    if not args.github_oauth or args.dry_run:
        return
    saved = paths.mcp_config_dir / "oauth-settings.json"
    known = getattr(args, "installed_oauth", False) and not args.force_reconfigure
    if not args.force_reconfigure and saved.exists():
        try:
            data = json.loads(load_secret_file(saved, "saved OAuth settings"))
            if data.get("schema") != 1 or not isinstance(data.get("options"), dict):
                raise ValueError
        except (ValueError, AttributeError):
            raise ToolError("saved OAuth settings are invalid") from None
        for field in GITHUB_OAUTH_FIELDS:
            name = "github_oauth_" + field
            if getattr(args, name) is None:
                setattr(args, name, data["options"].get(field))
        known = True
    prompts = SetupPrompts(args, input_fn)
    for field, label, default, required in (
        ("public_url", "Public OAuth HTTPS origin", None, True),
        ("client_id", "GitHub OAuth App Client ID", None, True),
        ("allowed_user_id", "Allowed numeric GitHub user ID", None, True),
        ("login_hint", "GitHub login hint (optional)", None, False),
        ("state_dir", "OAuth state directory", str(paths.oauth_state_path), True),
        ("access_token_ttl", "OAuth access token lifetime in seconds", 3600, True),
    ):
        name = "github_oauth_" + field
        if known and field in ("login_hint", "access_token_ttl") and getattr(args, name) is None:
            setattr(args, name, default)
            continue
        if getattr(args, name) is None:
            prompts.value(
                name, label, default=default, required=required, integer=field == "access_token_ttl"
            )
        if getattr(args, name) is None and required:
            if default is not None:
                setattr(args, name, default)
            else:
                raise ToolError("missing --github-oauth-" + field.replace("_", "-"))
    for field, label in (
        ("client_secret_file", "GitHub Client Secret file (blank: hidden input)"),
        ("signing_key_file", "OAuth signing key file (blank: generate)"),
    ):
        name = "github_oauth_" + field
        if getattr(args, name) is not None:
            continue
        prompts.value(name, label)
        if getattr(args, name):
            continue
        if field == "client_secret_file":
            value = resolve_secret(
                None,
                "--github-oauth-client-secret-file",
                "GitHub Client Secret: ",
                non_interactive=args.non_interactive,
                getpass_fn=getpass_fn,
            )
        else:
            value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        redactor.add(value)
        directory = paths.mcp_config_dir / "oauth-secrets"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ToolError("managed OAuth secrets directory must be private and owner-only")
        fd, filename = tempfile.mkstemp(prefix=field.removesuffix("_file") + "-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(value + "\n")
        setattr(args, name, filename)
        if not hasattr(args, "new_oauth_files"):
            args.new_oauth_files = []
        args.new_oauth_files.append(Path(filename))


def reconcile_owned_oauth(paths: InstallPaths, args: argparse.Namespace) -> None:
    if not args.force_reconfigure:
        return
    for state in managed_oauth_state_paths(paths):
        if not state.exists():
            continue
        if (
            any(parent.is_symlink() for parent in (state, *state.parents))
            or (state / ".lock").is_symlink()
        ):
            raise ToolError("refusing to reset linked OAuth state")
        with ProfileLock(state / ".lock"):
            remove_managed_path(state)
    secrets_dir = paths.mcp_config_dir / "oauth-secrets"
    retained = {
        Path(str(getattr(args, "github_oauth_" + field))).resolve()
        for field in ("client_secret_file", "signing_key_file")
        if args.github_oauth and getattr(args, "github_oauth_" + field, None)
    }
    if secrets_dir.is_symlink():
        raise ToolError("refusing to reset linked OAuth secrets")
    if secrets_dir.is_dir():
        for path in secrets_dir.iterdir():
            if path.resolve() not in retained:
                remove_managed_path(path)
        if not any(secrets_dir.iterdir()):
            secrets_dir.rmdir()
    if not args.github_oauth:
        (paths.mcp_config_dir / "oauth-settings.json").unlink(missing_ok=True)
    marker = paths.mcp_config_dir / "oauth-managed-state.json"
    marker.unlink(missing_ok=True)
    if args.github_oauth:
        state = Path(args.github_oauth_state_dir)
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state == paths.oauth_state_path:
            atomic_write(
                marker, json.dumps({"schema": 1, "path": str(state)}).encode() + b"\n", 0o600
            )


def prepare_oauth_service(
    runner: CommandRunner,
    paths: InstallPaths,
    args: argparse.Namespace,
    *,
    binary: Path | None = None,
    provision: bool = True,
) -> None:
    if not getattr(args, "github_oauth", False):
        return
    result = runner.run(
        [
            binary or paths.mcp_binary,
            "mcp",
            "oauth-check",
            *github_oauth_arguments(args),
            "--token-file",
            paths.token_file,
            "--auth-token-file",
            paths.mcp_auth_token_file,
            "--gpt-actions",
            "--gpt-actions-token-file",
            paths.gpt_actions_token_file,
            "--mcp-port",
            str(args.mcp_port),
            "--port",
            str(args.api_port),
            "--json",
        ],
        check=False,
    )
    if result.returncode != 0:
        # The structured CLI error names the invalid setting, never secret values.
        reason = ""
        try:
            failure = json.loads(result.stdout)
            message = failure.get("error") if isinstance(failure, dict) else None
            if isinstance(message, str) and message.isprintable():
                reason = ": " + message[:300]
        except ValueError:
            pass
        raise ToolError(
            "OAuth candidate/configuration check failed"
            + reason
            + "; service configuration was preserved"
        )
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("oauth_config_valid") is not True:
            raise ValueError
        state_path = Path(payload["state_dir"])
        endpoint = payload["endpoint"]
        if not isinstance(endpoint, str) or not endpoint.endswith("/claude/mcp"):
            raise ValueError
        origin = endpoint.removesuffix("/claude/mcp")
        public = urllib.parse.urlsplit(origin)
        if public.scheme != "https" or not public.netloc or public.path:
            raise ValueError
        if (
            not state_path.is_absolute()
            or state_path == Path(state_path.anchor)
            or len(state_path.parts) < 3
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ToolError("OAuth check returned an invalid state directory") from None
    args.github_oauth_state_dir = state_path
    args.github_oauth_public_base_url = origin
    if provision:
        state_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = state_path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ToolError("OAuth state directory must be private and owned by the service user")
        if state_path == paths.oauth_state_path:
            atomic_write(
                paths.mcp_config_dir / "oauth-managed-state.json",
                json.dumps({"schema": 1, "path": str(state_path)}).encode("utf-8") + b"\n",
                0o600,
            )
    if state_path != paths.oauth_state_path:
        LOG.info("custom OAuth state is operator-owned and preserved by purge: %s", state_path)


def smoke_test_oauth_service(port: int, public_origin: str, *, timeout: float = 5.0) -> None:
    """Prove discovery and anonymous denial without a GitHub account or token."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Host": urllib.parse.urlsplit(public_origin).netloc}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/claude/mcp",
        data=b"{}",
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout):
            raise ToolError("OAuth resource accepted an anonymous request")
    except urllib.error.HTTPError as exc:
        try:
            challenge = exc.headers.get("WWW-Authenticate", "")
            if (
                exc.code != 401
                or public_origin + "/.well-known/oauth-protected-resource/claude/mcp"
                not in challenge
            ):
                raise ToolError("OAuth resource challenge smoke check failed")
        finally:
            exc.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ToolError("OAuth resource smoke probe failed") from None
    discovery = urllib.request.Request(
        f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/claude/mcp",
        headers=headers,
    )
    try:
        with opener.open(discovery, timeout=timeout) as response:
            raw = response.read(65537)
        if len(raw) > 65536 or json.loads(raw).get("resource") != public_origin + "/claude/mcp":
            raise ValueError
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        raise ToolError("OAuth discovery smoke check failed") from None
    LOG.info("OAuth smoke test passed: discovery and anonymous authentication challenge verified")
