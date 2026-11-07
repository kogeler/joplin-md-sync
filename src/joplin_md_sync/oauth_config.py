"""Dependency-free, read-only validation of the explicit GitHub OAuth config."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import ipaddress
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from joplin_md_sync.auth import read_protected_token_line

OAUTH_CLI_FIELDS = (
    ("public_url", "public_base_url"),
    ("client_id", "client_id"),
    ("client_secret_file", "client_secret_file"),
    ("signing_key_file", "signing_key_file"),
    ("allowed_user_id", "allowed_github_user_id"),
    ("login_hint", "allowed_github_login"),
    ("state_dir", "state_dir"),
    ("access_token_ttl", "access_token_ttl_seconds"),
)


def add_oauth_cli_arguments(
    parser: argparse.ArgumentParser, *, default_enabled: bool = False
) -> None:
    parser.add_argument(
        "--github-oauth",
        action="store_true",
        default=default_enabled,
        help="enable GitHub OAuth on the existing HTTP listener",
    )
    help_text = {
        "public_url": "public HTTPS origin for OAuth discovery and callbacks",
        "client_id": "GitHub OAuth App Client ID",
        "client_secret_file": "protected GitHub OAuth App Client Secret file",
        "signing_key_file": "protected file containing a base64-encoded 32-byte signing key",
        "allowed_user_id": "single permitted positive numeric GitHub user ID",
        "login_hint": "optional GitHub login hint; authorization always uses the numeric ID",
        "state_dir": "absolute path to the private OAuth state directory",
        "access_token_ttl": "OAuth access token lifetime, 600-3600 seconds (default: 3600)",
    }
    for option, _ in OAUTH_CLI_FIELDS:
        parser.add_argument(
            "--github-oauth-" + option.replace("_", "-"),
            type=int if option == "access_token_ttl" else str,
            metavar="SECONDS"
            if option == "access_token_ttl"
            else ("PATH" if option.endswith("_file") or option == "state_dir" else "VALUE"),
            help=help_text[option],
        )


def decode_signing_key(line: bytes) -> bytes:
    """Decode either Base64 alphabet, with or without padding, as configured."""
    return base64.b64decode(line + b"=" * (-len(line) % 4), altchars=b"-_", validate=True)


def matches_signing_key(value: str, key: bytes) -> bool:
    """Whether a credential is any accepted text encoding of the signing key."""
    try:
        decoded = decode_signing_key(value.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return hmac.compare_digest(decoded, key)


def signing_key_encodings(key: bytes) -> list[str]:
    """Accepted text forms of a key, for log redaction."""
    forms = [base64.urlsafe_b64encode(key), base64.standard_b64encode(key)]
    return [text.decode("ascii") for form in forms for text in (form, form.rstrip(b"="))]


def lookup_github_user_id(login: str) -> str:
    """Resolve a public login for setup; never use the login as an access rule."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login):
        raise ValueError("Provide a GitHub username, not a URL")
    request = urllib.request.Request(
        "https://api.github.com/users/" + login,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "joplin-md-sync"},
    )
    try:
        # Fixed HTTPS API origin; the validated username cannot introduce a URL.
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError
        identity = json.loads(raw)["id"]
        if type(identity) is not int or identity <= 0:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "GitHub account lookup failed; supply a verified numeric ID manually"
        ) from None
    return str(identity)


@dataclass(frozen=True, repr=False)
class OAuthConfig:
    public_base_url: str
    client_id: str
    client_secret: str
    signing_key: bytes
    allowed_github_user_id: str
    state_dir: Path
    allowed_github_login: str | None = None
    access_token_ttl_seconds: int = 3600

    @classmethod
    def from_cli(cls, args: argparse.Namespace) -> OAuthConfig:
        values = {
            field: getattr(args, "github_oauth_" + option) for option, field in OAUTH_CLI_FIELDS
        }
        if values["access_token_ttl_seconds"] is None:
            values["access_token_ttl_seconds"] = 3600
        return cls.from_values(values)

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> OAuthConfig:
        required = {
            "public_base_url",
            "client_id",
            "client_secret_file",
            "signing_key_file",
            "allowed_github_user_id",
            "state_dir",
        }
        optional = {"allowed_github_login", "access_token_ttl_seconds"}
        if not required <= values.keys() or values.keys() - required - optional:
            raise ValueError("OAuth CLI options contain missing or unknown settings")
        if any(type(values[key]) is not str or not values[key] for key in required):
            raise ValueError("Required GitHub OAuth CLI options must be non-empty strings")
        origin = values["public_base_url"]
        try:
            split = urllib.parse.urlsplit(origin)
            valid = (
                split.scheme == "https"
                and bool(split.hostname)
                and split.username is None
                and split.password is None
                and not split.query
                and not split.fragment
                and split.path in ("", "/")
                and (split.port is None or 1 <= split.port <= 65535)
            )
        except ValueError:
            valid = False
        if not valid or any(char.isspace() for char in origin) or "?" in origin or "#" in origin:
            raise ValueError("OAuth public_base_url must be an HTTPS origin")
        hostname = (split.hostname or "").encode("idna").decode("ascii").lower()
        if ":" in hostname:
            hostname = "[" + ipaddress.IPv6Address(hostname).compressed + "]"
        elif not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        ):
            raise ValueError("OAuth public_base_url has an invalid hostname")
        origin = "https://" + hostname + (f":{split.port}" if split.port not in (None, 443) else "")
        user_id = values["allowed_github_user_id"]
        if not re.fullmatch(r"[1-9][0-9]{0,19}", user_id):
            raise ValueError("allowed_github_user_id must be one positive ASCII decimal ID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", values["client_id"]):
            raise ValueError("Invalid GitHub OAuth client ID")
        login = values.get("allowed_github_login")
        if login is not None and (
            type(login) is not str
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login)
        ):
            raise ValueError("Invalid GitHub login hint")
        ttl = values.get("access_token_ttl_seconds", 3600)
        if type(ttl) is not int or not 600 <= ttl <= 3600:
            raise ValueError("OAuth access token TTL must be an integer between 600 and 3600")
        paths = {
            key: Path(values[key])
            for key in ("client_secret_file", "signing_key_file", "state_dir")
        }
        if any(not item.is_absolute() for item in paths.values()):
            raise ValueError("OAuth secret and state paths must be absolute")
        if any(part.rstrip(" .").casefold() == ".joplin-sync" for part in paths["state_dir"].parts):
            raise ValueError("OAuth state must be separate from sync state")
        try:
            secret = read_protected_token_line(
                paths["client_secret_file"], label="GitHub client secret"
            ).decode("ascii")
            key_line = read_protected_token_line(
                paths["signing_key_file"], label="OAuth signing key"
            )
            key = decode_signing_key(key_line)
        except (UnicodeDecodeError, binascii.Error):
            raise ValueError("Invalid OAuth secret or signing key format") from None
        if len(key) != 32:
            raise ValueError("OAuth signing key must encode exactly 32 random bytes")
        if not re.fullmatch(r"[!-~]{1,512}", secret):
            raise ValueError(
                "GitHub client secret must be bounded printable ASCII without whitespace"
            )
        if secret.encode() == key_line or matches_signing_key(secret, key):
            raise ValueError("GitHub client secret and OAuth signing key must differ")
        return cls(
            origin.rstrip("/"),
            values["client_id"],
            secret,
            key,
            user_id,
            paths["state_dir"],
            login,
            ttl,
        )
