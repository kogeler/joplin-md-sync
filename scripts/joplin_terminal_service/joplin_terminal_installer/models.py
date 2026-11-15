"""Joplin Terminal installation: models."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("joplin-terminal-installer")


@dataclass(frozen=True)
class SyncTargetSpec:
    name: str
    target_id: int
    label: str
    location_label: str | None = None
    username_label: str | None = None
    secret_label: str | None = None
    browser_auth: bool = False
    saml_auth: bool = False
    credentials_optional: bool = False
    auth_setting: str | None = None


SYNC_TARGETS = {
    spec.name: spec
    for spec in (
        SyncTargetSpec("none", 0, "No synchronization"),
        SyncTargetSpec("filesystem", 2, "File system", "absolute directory"),
        SyncTargetSpec(
            "onedrive",
            3,
            "OneDrive",
            browser_auth=True,
            auth_setting="sync.3.auth",
        ),
        SyncTargetSpec(
            "nextcloud",
            5,
            "Nextcloud",
            "Nextcloud WebDAV URL",
            "Nextcloud username",
            "Nextcloud password",
        ),
        SyncTargetSpec(
            "webdav",
            6,
            "WebDAV",
            "WebDAV URL",
            "WebDAV username",
            "WebDAV password",
            credentials_optional=True,
        ),
        SyncTargetSpec(
            "dropbox",
            7,
            "Dropbox",
            browser_auth=True,
            auth_setting="sync.7.auth",
        ),
        SyncTargetSpec(
            "s3",
            8,
            "S3",
            "S3 bucket",
            "S3 access key",
            "S3 secret key",
        ),
        SyncTargetSpec(
            "joplin-server",
            9,
            "Joplin Server",
            "Joplin Server URL",
            "Joplin Server email",
            "Joplin Server password",
        ),
        SyncTargetSpec(
            "joplin-cloud",
            10,
            "Joplin Cloud",
            browser_auth=True,
            auth_setting="sync.10.password",
        ),
        SyncTargetSpec(
            "joplin-server-saml",
            11,
            "Joplin Server (SAML)",
            "Joplin Server URL",
            saml_auth=True,
        ),
    )
}


@dataclass(frozen=True)
class SyncSetting:
    name: str
    value: str
    secret: bool = False


@dataclass(frozen=True)
class SyncConfiguration:
    target: SyncTargetSpec
    settings: tuple[SyncSetting, ...]

    @property
    def public_settings(self) -> tuple[SyncSetting, ...]:
        return tuple(setting for setting in self.settings if not setting.secret)


@dataclass(frozen=True)
class InstallPaths:
    prefix: Path
    npm_prefix: Path
    launcher: Path
    profile_dir: Path
    config_dir: Path
    token_file: Path
    mcp_binary: Path
    mcp_config_dir: Path
    mcp_auth_token_file: Path
    gpt_actions_token_file: Path
    state_dir: Path
    lock_file: Path
    deploy_dir: Path
    unit_path: Path
    adapter_unit_path: Path

    @property
    def oauth_state_path(self) -> Path:
        return self.state_dir.parent / "joplin-md-sync" / "oauth"

    @property
    def mcp_receipt_file(self) -> Path:
        """Private record of the adapter executable this installer placed."""
        return self.state_dir / "joplin-md-sync-receipt.json"


@dataclass(frozen=True)
class Dependencies:
    node: Path
    npm: Path
    systemctl: Path
    node_version: str
    npm_version: str
    loginctl: Path = Path("/usr/bin/loginctl")


@dataclass
class E2eeResult:
    exit_code: int
    output: bytes
    prompts: int
    timed_out: bool = False


GITHUB_OAUTH_FIELDS = (
    "public_url",
    "client_id",
    "client_secret_file",
    "signing_key_file",
    "allowed_user_id",
    "login_hint",
    "state_dir",
    "access_token_ttl",
)


SYNC_OPTIONS = frozenset(
    {
        "sync_target",
        "sync_location",
        "sync_username",
        "sync_username_file",
        "sync_secret_file",
        "s3_endpoint",
        "s3_region",
        "s3_force_path_style",
    }
)


ENVIRONMENT_OPTIONS = {
    "installer_ref": "JOPLIN_TERMINAL_INSTALLER_REF",
    "sync_target": "JOPLIN_SYNC_TARGET",
    "sync_location": "JOPLIN_SYNC_LOCATION",
    "sync_username": "JOPLIN_SYNC_USERNAME",
    "sync_username_file": "JOPLIN_SYNC_USERNAME_FILE",
    "sync_secret_file": "JOPLIN_SYNC_SECRET_FILE",
    "s3_endpoint": "JOPLIN_S3_ENDPOINT",
    "s3_region": "JOPLIN_S3_REGION",
    "e2ee_password_file": "JOPLIN_E2EE_PASSWORD_FILE",
    "api_port": "JOPLIN_API_PORT",
    "mcp_port": "JOPLIN_MCP_PORT",
    "sync_interval": "JOPLIN_SYNC_INTERVAL",
    "profile_dir": "JOPLIN_PROFILE_DIR",
    "joplin_prefix": "JOPLIN_INSTALL_PREFIX",
    "joplin_version": "JOPLIN_VERSION",
    "joplin_md_sync_version": "JOPLIN_MD_SYNC_VERSION",
    "github_oauth": "JOPLIN_GITHUB_OAUTH",
    **{
        "github_oauth_" + field: "JOPLIN_GITHUB_OAUTH_" + field.upper()
        for field in GITHUB_OAUTH_FIELDS
    },
}
