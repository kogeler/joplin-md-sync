"""Private fixture credentials and explicit public OAuth CLI arguments."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from joplin_md_sync.oauth_config import OAUTH_CLI_FIELDS, OAuthConfig

# Ambient connection settings would conflict with explicit fixture files and
# would hand an operator's real Joplin token to a test child process.
AMBIENT_CONNECTION_VARIABLES = ("JOPLIN_TOKEN", "JOPLIN_BASE_URL", "JOPLIN_PORT")


def child_environment() -> dict[str, str]:
    """Return the inherited environment without ambient Joplin settings."""
    return {
        name: value
        for name, value in os.environ.items()
        if name not in AMBIENT_CONNECTION_VARIABLES
    }


def protected_file(path: Path, value: str) -> Path:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class OAuthArguments:
    def __init__(self, directory: Path, public_url: str = "https://example.invalid"):
        self.directory = directory
        self.secret_path = protected_file(directory / "github-secret", "fixture-secret")
        self.signing_path = protected_file(
            directory / "oauth-key", base64.urlsafe_b64encode(os.urandom(32)).decode()
        )
        self.values = {
            "public_base_url": public_url,
            "client_id": "fixture-client",
            "client_secret_file": str(self.secret_path),
            "signing_key_file": str(self.signing_path),
            "allowed_github_user_id": "42",
            "state_dir": str(directory / "state"),
        }

    def argv(self, *, enabled: bool = True) -> list[str]:
        result = ["--github-oauth"] if enabled else []
        for option, field in OAUTH_CLI_FIELDS:
            if self.values.get(field) is not None:
                result.extend(
                    ["--github-oauth-" + option.replace("_", "-"), str(self.values[field])]
                )
        return result

    def config(self) -> OAuthConfig:
        return OAuthConfig.from_values(self.values)
