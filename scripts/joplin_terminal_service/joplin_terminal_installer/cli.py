"""Joplin Terminal installation: cli."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

from .common import (
    ADAPTER_SERVICE_NAME,
    DEFAULT_API_PORT,
    DEFAULT_JOPLIN_VERSION,
    DEFAULT_MCP_PORT,
    DEFAULT_MCP_VERSION,
    DEFAULT_SYNC_INTERVAL,
    SERVICE_NAME,
    SUPPORTED_SYNC_INTERVALS,
    ToolError,
    default_config_dir,
    default_profile_dir,
    default_state_dir,
    lock_path_for_profile,
    resolved_path,
)
from .models import (
    ENVIRONMENT_OPTIONS,
    GITHUB_OAUTH_FIELDS,
    SYNC_TARGETS,
    InstallPaths,
    SyncTargetSpec,
)

LOG = logging.getLogger("joplin-terminal-installer")


_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


def terminal_input(prompt: str) -> str:
    """Read a non-secret answer from the controlling TTY, not installer stdin."""
    try:
        with open("/dev/tty", "r+b", buffering=0) as terminal:
            terminal.write(prompt.encode("utf-8", errors="replace"))
            answer = terminal.readline()
    except OSError:
        try:
            return input(prompt)
        except (EOFError, OSError):
            raise ToolError(
                "interactive input is unavailable; run from a terminal or use "
                "--non-interactive with explicit options"
            ) from None
    if not answer:
        raise ToolError(
            "interactive terminal closed; rerun from a terminal or use "
            "--non-interactive with explicit options"
        )
    return answer.decode("utf-8", errors="replace").rstrip("\r\n")


def _env_int(env: Mapping[str, str], name: str, fallback: int) -> int:
    raw = env.get(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise ToolError(f"{name} must be an integer, got {raw!r}") from None


class InstallerArgumentParser(argparse.ArgumentParser):
    def parse_args(
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> argparse.Namespace:
        tokens = list(sys.argv[1:] if args is None else args)
        result = super().parse_args(tokens, namespace)
        result.explicit_options = set(result.environment_options)
        result.command_line_options = set()
        for action in self._actions:
            if any(token.split("=", 1)[0] in action.option_strings for token in tokens):
                result.explicit_options.add(action.dest)
                result.command_line_options.add(action.dest)
        return result


def github_oauth_arguments(args: argparse.Namespace) -> list[str]:
    if not getattr(args, "github_oauth", False):
        return []
    result = ["--github-oauth"]
    for field in GITHUB_OAUTH_FIELDS:
        value = getattr(args, "github_oauth_" + field, None)
        if value is not None:
            result.extend(["--github-oauth-" + field.replace("_", "-"), str(value)])
    return result


def build_parser(env: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    values = os.environ if env is None else env
    parser = InstallerArgumentParser(
        allow_abbrev=False,
        description=(
            "Install an isolated Joplin Terminal with any supported sync target, install "
            "the released joplin-md-sync service with generated Actions and MCP "
            "credentials, create systemd user services, or purge the complete managed "
            "local installation."
        ),
    )
    parser.set_defaults(
        environment_options={
            name for name, variable in ENVIRONMENT_OPTIONS.items() if variable in values
        }
    )
    parser.add_argument(
        "--installer-ref",
        "--ref",
        default=values.get("JOPLIN_TERMINAL_INSTALLER_REF", "main"),
        help="installer source branch, tag or commit SHA (default: main); fetch the entry script from the same ref",
    )
    parser.add_argument(
        "--sync-target",
        choices=tuple(SYNC_TARGETS),
        default=values.get("JOPLIN_SYNC_TARGET"),
        help="Joplin sync backend; prompted when not configured or supplied",
    )
    parser.add_argument(
        "--sync-location",
        default=values.get("JOPLIN_SYNC_LOCATION"),
        help="filesystem directory, server URL, or S3 bucket required by the target",
    )
    parser.add_argument(
        "--sync-username",
        default=values.get("JOPLIN_SYNC_USERNAME"),
        help="Nextcloud/WebDAV username, Joplin Server email, or S3 access key",
    )
    parser.add_argument(
        "--sync-username-file",
        default=values.get("JOPLIN_SYNC_USERNAME_FILE"),
        help="optional protected username or S3 access-key file (S3 default: hidden prompt)",
    )
    parser.add_argument(
        "--sync-secret-file",
        default=values.get("JOPLIN_SYNC_SECRET_FILE"),
        help="protected one-line file containing the target password or S3 secret key",
    )
    parser.add_argument(
        "--s3-endpoint",
        default=values.get("JOPLIN_S3_ENDPOINT"),
        help="S3-compatible HTTP(S) endpoint (default: https://s3.amazonaws.com/)",
    )
    parser.add_argument(
        "--s3-region",
        default=values.get("JOPLIN_S3_REGION"),
        help="S3 region; required for the S3 target",
    )
    parser.add_argument(
        "--s3-force-path-style",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable path-style S3 requests (default: disabled)",
    )
    parser.add_argument(
        "--e2ee-password-file",
        default=values.get("JOPLIN_E2EE_PASSWORD_FILE"),
        help="protected one-line file containing the existing Joplin E2EE password",
    )
    parser.add_argument(
        "--profile-dir",
        default=values.get("JOPLIN_PROFILE_DIR", str(default_profile_dir(values))),
        help="dedicated Joplin Terminal profile (default: XDG data home/joplin-agent/profile)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=_env_int(values, "JOPLIN_API_PORT", DEFAULT_API_PORT),
        help=f"loopback Joplin Data API port (default: {DEFAULT_API_PORT})",
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=_env_int(values, "JOPLIN_SYNC_INTERVAL", DEFAULT_SYNC_INTERVAL),
        help=f"recurrent sync interval in seconds (default: {DEFAULT_SYNC_INTERVAL})",
    )
    parser.add_argument(
        "--joplin-version",
        default=values.get("JOPLIN_VERSION", DEFAULT_JOPLIN_VERSION),
        help=f"Joplin npm version or latest (default: {DEFAULT_JOPLIN_VERSION})",
    )
    parser.add_argument(
        "--joplin-prefix",
        default=values.get(
            "JOPLIN_INSTALL_PREFIX", str(Path(values.get("HOME", str(Path.home()))) / ".local")
        ),
        help="installation prefix for managed executables and npm data (default: ~/.local)",
    )
    parser.add_argument(
        "--joplin-md-sync-version",
        default=values.get("JOPLIN_MD_SYNC_VERSION", DEFAULT_MCP_VERSION),
        help=f"joplin-md-sync release version or latest (default: {DEFAULT_MCP_VERSION})",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=_env_int(values, "JOPLIN_MCP_PORT", DEFAULT_MCP_PORT),
        help=f"combined MCP and Actions listener port (default: {DEFAULT_MCP_PORT})",
    )
    parser.add_argument(
        "--allow-remote-mcp",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="bind the authenticated combined adapter to 0.0.0.0 instead of loopback",
    )
    parser.add_argument(
        "--github-oauth",
        action="store_true",
        default=values.get("JOPLIN_GITHUB_OAUTH", "").lower() in {"1", "true", "yes"},
        help="enable GitHub OAuth on the existing adapter port",
    )
    oauth_help = {
        "public_url": "public HTTPS origin for OAuth",
        "client_id": "GitHub OAuth App Client ID",
        "client_secret_file": "protected GitHub OAuth Client Secret file",
        "signing_key_file": "protected OAuth signing key file",
        "allowed_user_id": "single allowed numeric GitHub user ID",
        "login_hint": "optional GitHub login hint, not an access rule",
        "state_dir": "absolute private OAuth state directory",
        "access_token_ttl": "OAuth access lifetime in seconds (600-3600)",
    }
    for field in GITHUB_OAUTH_FIELDS:
        parser.add_argument(
            "--github-oauth-" + field.replace("_", "-"),
            type=int if field == "access_token_ttl" else str,
            default=values.get("JOPLIN_GITHUB_OAUTH_" + field.upper()),
            help=oauth_help[field],
        )
    parser.add_argument(
        "--no-github-oauth",
        action="store_true",
        help="disable installed GitHub OAuth while preserving config and grants",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "update both Joplin and joplin-md-sync; --joplin-version and "
            "--joplin-md-sync-version independently override latest"
        ),
    )
    parser.add_argument(
        "--no-enable-service",
        action="store_true",
        help="install units without enabling automatic startup",
    )
    parser.add_argument(
        "--enable-service",
        action="store_false",
        dest="no_enable_service",
        help="enable automatic startup of both services (interactive default: no)",
    )
    parser.add_argument(
        "--no-start-service",
        action="store_true",
        help="leave both services stopped and skip live smoke tests",
    )
    parser.add_argument(
        "--start-service",
        action="store_false",
        dest="no_start_service",
        help="start both services and run live smoke tests (interactive default: no)",
    )
    parser.set_defaults(no_start_service=False, no_enable_service=False)
    parser.add_argument(
        "--reconfigure",
        "--force-reconfigure",
        dest="force_reconfigure",
        action="store_true",
        help="rebuild configuration from this run; remove disabled managed feature settings/secrets",
    )
    parser.add_argument(
        "--enable-linger",
        action=argparse.BooleanOptionalAction,
        help="enable systemd user lingering with loginctl when it is currently disabled",
    )
    parser.add_argument(
        "--clean",
        "--purge",
        dest="purge",
        action="store_true",
        help="uninstall both services and delete managed local data without prompts",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="compatibility option for --purge; --clean/--purge already runs without prompts",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; require every needed static value and secret file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate arguments and print a plan without reading secrets or changing state",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable redacted debug logging",
    )
    return parser


def build_paths(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> InstallPaths:
    values = os.environ if env is None else env
    prefix = resolved_path(args.joplin_prefix)
    profile_dir = resolved_path(args.profile_dir)
    config_dir = default_config_dir(values)
    state_dir = default_state_dir(values)
    npm_prefix = prefix / "share" / "joplin-agent" / "npm"
    config_home = config_dir.parent
    mcp_config_dir = resolved_path(config_home / "joplin-md-sync")
    return InstallPaths(
        prefix=prefix,
        npm_prefix=npm_prefix,
        launcher=prefix / "bin" / "joplin",
        profile_dir=profile_dir,
        config_dir=config_dir,
        token_file=config_dir / "api-token",
        mcp_binary=prefix / "bin" / "joplin-md-sync",
        mcp_config_dir=mcp_config_dir,
        mcp_auth_token_file=mcp_config_dir / "mcp-token",
        gpt_actions_token_file=mcp_config_dir / "gpt-actions-token",
        state_dir=state_dir,
        lock_file=lock_path_for_profile(profile_dir, state_dir),
        deploy_dir=prefix / "lib" / "joplin-terminal-service",
        unit_path=config_home / "systemd" / "user" / SERVICE_NAME,
        adapter_unit_path=config_home / "systemd" / "user" / ADAPTER_SERVICE_NAME,
    )


def validate_version(value: str, option: str) -> None:
    if value != "latest" and not _VERSION_RE.fullmatch(value):
        raise ToolError(f"{option} must be 'latest' or a concrete version such as 3.6.2")


def validate_http_url(value: str, option: str) -> None:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ToolError(f"{option} must not contain control characters")
    try:
        split = urllib.parse.urlsplit(value)
        hostname = split.hostname
        _port = split.port
    except ValueError:
        raise ToolError(f"{option} must be a valid absolute HTTP(S) URL") from None
    if split.scheme not in ("http", "https") or not hostname:
        raise ToolError(f"{option} must be an absolute HTTP(S) URL")
    if split.username or split.password:
        raise ToolError(f"{option} must not contain embedded credentials")
    if split.query or split.fragment:
        raise ToolError(f"{option} must not contain a query string or fragment")
    if split.scheme != "https":
        LOG.warning("%s does not use HTTPS", option)


def selected_sync_target(args: argparse.Namespace) -> SyncTargetSpec:
    if not args.sync_target:
        raise ToolError(
            "--sync-target or JOPLIN_SYNC_TARGET is required; choose one of: "
            + ", ".join(SYNC_TARGETS)
        )
    return SYNC_TARGETS[args.sync_target]


def validate_sync_args(args: argparse.Namespace) -> None:
    target = selected_sync_target(args)
    if args.sync_username and args.sync_username_file:
        raise ToolError("use exactly one of --sync-username and --sync-username-file")
    has_username = bool(args.sync_username or args.sync_username_file)
    if target.location_label and not args.sync_location:
        raise ToolError(
            f"--sync-location or JOPLIN_SYNC_LOCATION is required for {target.label} "
            f"({target.location_label})"
        )
    if (
        target.username_label
        and not target.credentials_optional
        and not has_username
        and (target.target_id != 8 or args.non_interactive)
    ):
        raise ToolError(
            f"--sync-username, --sync-username-file or JOPLIN_SYNC_USERNAME is required for {target.label} "
            f"({target.username_label})"
        )
    if target.secret_label and not target.credentials_optional:
        if (
            args.non_interactive
            and not args.sync_secret_file
            and not getattr(args, "saved_sync_secret", None)
        ):
            raise ToolError(
                f"--sync-secret-file or JOPLIN_SYNC_SECRET_FILE is required for "
                f"non-interactive {target.label} installation"
            )
    if target.credentials_optional:
        if args.sync_secret_file and not has_username:
            raise ToolError("WebDAV --sync-secret-file requires --sync-username")
        if (
            args.non_interactive
            and has_username
            and not args.sync_secret_file
            and not getattr(args, "saved_sync_secret", None)
        ):
            raise ToolError("non-interactive WebDAV credentials require --sync-secret-file")
    if target.browser_auth or target.saml_auth:
        if args.non_interactive and not getattr(args, "saved_browser_auth", False):
            raise ToolError(f"{target.label} authentication requires an interactive terminal")
    if target.target_id == 2:
        location = Path(args.sync_location).expanduser()
        if not location.is_absolute():
            raise ToolError("filesystem --sync-location must be an absolute path")
        if not location.is_dir():
            raise ToolError("filesystem --sync-location must be an existing directory")
        if not os.access(location, os.W_OK | os.X_OK):
            raise ToolError("filesystem --sync-location must be writable and searchable")
        if has_username or args.sync_secret_file:
            raise ToolError("filesystem sync does not accept static credentials")
    elif target.target_id in (5, 6, 9, 11):
        validate_http_url(args.sync_location, "--sync-location")
        if target.target_id == 11 and (has_username or args.sync_secret_file):
            raise ToolError("Joplin Server SAML does not accept static credentials")
    elif target.target_id == 8:
        if not args.s3_region:
            raise ToolError("--s3-region or JOPLIN_S3_REGION is required for S3")
        endpoint = args.s3_endpoint or "https://s3.amazonaws.com/"
        validate_http_url(endpoint, "--s3-endpoint")
    elif (
        any(value for value in (args.sync_location, has_username, args.sync_secret_file))
        or args.s3_endpoint is not None
        or args.s3_region is not None
        or args.s3_force_path_style is not None
    ):
        raise ToolError(f"{target.label} does not accept static sync credentials")

    if target.target_id != 8 and (
        args.s3_endpoint is not None
        or args.s3_region is not None
        or args.s3_force_path_style is not None
    ):
        raise ToolError("S3 options are only valid with --sync-target s3")


def validate_args(args: argparse.Namespace) -> None:
    if args.purge:
        if args.upgrade:
            raise ToolError("--purge cannot be combined with --upgrade")
        return
    if args.github_oauth and args.no_github_oauth:
        raise ToolError("--github-oauth cannot be combined with --no-github-oauth")
    if not args.github_oauth and any(
        getattr(args, "github_oauth_" + field, None) is not None for field in GITHUB_OAUTH_FIELDS
    ):
        raise ToolError("GitHub OAuth options require --github-oauth")
    if args.yes:
        raise ToolError("--yes is only valid with --purge")
    validate_version(args.joplin_version, "Joplin version")
    validate_version(args.joplin_md_sync_version, "joplin-md-sync version")
    if not 1 <= args.api_port <= 65535:
        raise ToolError("--api-port must be between 1 and 65535")
    if not 1 <= args.mcp_port <= 65535:
        raise ToolError("--mcp-port must be between 1 and 65535")
    if args.api_port == args.mcp_port:
        raise ToolError("--api-port and --mcp-port must be different")
    if args.sync_interval not in SUPPORTED_SYNC_INTERVALS:
        allowed = ", ".join(str(value) for value in SUPPORTED_SYNC_INTERVALS)
        raise ToolError(f"--sync-interval must be one of: {allowed}")
    if args.upgrade or (args.dry_run and not args.sync_target):
        return
    validate_sync_args(args)
