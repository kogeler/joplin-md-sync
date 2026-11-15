"""Joplin Terminal installation: credentials."""

from __future__ import annotations

import base64
import binascii
import getpass
import hmac
import logging
import os
import re
import secrets
import stat
import warnings
from collections.abc import Callable

from .common import (
    SecretRedactor,
    ToolError,
    absolute_path,
    atomic_write,
)
from .models import InstallPaths

LOG = logging.getLogger("joplin-terminal-installer")


def hidden_input(prompt: str) -> str:
    """Never fall back to an echoing password prompt when no private TTY exists."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (EOFError, OSError, getpass.GetPassWarning):
            raise ToolError(
                "hidden terminal input is unavailable; use a protected credential file "
                "or run from a terminal"
            ) from None


MAX_SERVICE_BEARER_TOKEN_CHARS = 1024


def validate_service_bearer_token(token: str, *, label: str) -> None:
    if not token or len(token) > MAX_SERVICE_BEARER_TOKEN_CHARS:
        raise ToolError(
            f"{label} bearer token must contain at most {MAX_SERVICE_BEARER_TOKEN_CHARS} characters"
        )
    if re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", token) is None:
        raise ToolError(f"{label} bearer token must use URL-safe Base64 encoding")
    try:
        encoded = token.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise ToolError(f"{label} bearer token must use URL-safe Base64 encoding") from None
    if len(decoded) < 32:
        raise ToolError(f"{label} bearer token must encode at least 32 bytes")


def constant_time_token_equal(left: str, right: str) -> bool:
    """Compare ASCII token values without raising on corrupted local input."""
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeEncodeError:
        return False


def validate_mcp_auth_token(token: str) -> None:
    validate_service_bearer_token(token, label="MCP")


def load_mcp_auth_token(paths: InstallPaths, redactor: SecretRedactor) -> str | None:
    if not paths.mcp_auth_token_file.exists():
        return None
    if paths.mcp_auth_token_file.is_symlink() or not paths.mcp_auth_token_file.is_file():
        raise ToolError(f"MCP auth token path is not a file: {paths.mcp_auth_token_file}")
    if paths.mcp_auth_token_file.stat().st_size > MAX_SERVICE_BEARER_TOKEN_CHARS + 2:
        raise ToolError("existing MCP auth token file is too large")
    try:
        token = paths.mcp_auth_token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("existing MCP auth token file cannot be read") from None
    validate_mcp_auth_token(token)
    paths.mcp_auth_token_file.chmod(0o600)
    redactor.add(token)
    return token


def resolve_mcp_auth_token(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> str:
    if paths.mcp_auth_token_file.exists() and not paths.mcp_auth_token_file.is_file():
        raise ToolError(f"MCP auth token path is not a file: {paths.mcp_auth_token_file}")
    if paths.mcp_auth_token_file.is_file():
        token = load_mcp_auth_token(paths, redactor)
        if token is None:  # guarded by is_file(); keeps the type explicit
            raise ToolError("existing MCP auth token file cannot be read")
        LOG.info("preserving existing MCP bearer token")
        return token
    token = secrets.token_urlsafe(32)
    validate_mcp_auth_token(token)
    redactor.add(token)
    LOG.info("generated a new MCP bearer token")
    return token


def configure_mcp_auth_token(paths: InstallPaths, token: str) -> None:
    validate_mcp_auth_token(token)
    content = f"{token}\n".encode()
    if paths.mcp_auth_token_file.is_file() and paths.mcp_auth_token_file.read_bytes() == content:
        paths.mcp_auth_token_file.chmod(0o600)
        LOG.info("protected MCP bearer token is unchanged")
        return
    atomic_write(paths.mcp_auth_token_file, content, 0o600)
    LOG.info("stored protected MCP bearer token")


def validate_gpt_actions_token(token: str) -> None:
    validate_service_bearer_token(token, label="GPT Actions")


def load_gpt_actions_token(paths: InstallPaths, redactor: SecretRedactor) -> str | None:
    if not paths.gpt_actions_token_file.exists():
        return None
    if paths.gpt_actions_token_file.is_symlink() or not paths.gpt_actions_token_file.is_file():
        raise ToolError(f"GPT Actions token path is not a file: {paths.gpt_actions_token_file}")
    if paths.gpt_actions_token_file.stat().st_size > MAX_SERVICE_BEARER_TOKEN_CHARS + 2:
        raise ToolError("existing GPT Actions token file is too large")
    try:
        token = paths.gpt_actions_token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("existing GPT Actions token file cannot be read") from None
    validate_gpt_actions_token(token)
    paths.gpt_actions_token_file.chmod(0o600)
    redactor.add(token)
    return token


def resolve_gpt_actions_token(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> str:
    if paths.gpt_actions_token_file.exists() and not paths.gpt_actions_token_file.is_file():
        raise ToolError(f"GPT Actions token path is not a file: {paths.gpt_actions_token_file}")
    if paths.gpt_actions_token_file.is_file():
        token = load_gpt_actions_token(paths, redactor)
        if token is None:  # guarded by is_file(); keeps the type explicit
            raise ToolError("existing GPT Actions token file cannot be read")
        LOG.info("preserving existing GPT Actions bearer token")
        return token
    token = secrets.token_urlsafe(32)
    validate_gpt_actions_token(token)
    redactor.add(token)
    LOG.info("generated a new GPT Actions bearer token")
    return token


def configure_gpt_actions_token(paths: InstallPaths, token: str) -> None:
    validate_gpt_actions_token(token)
    content = f"{token}\n".encode()
    if (
        paths.gpt_actions_token_file.is_file()
        and paths.gpt_actions_token_file.read_bytes() == content
    ):
        paths.gpt_actions_token_file.chmod(0o600)
        LOG.info("protected GPT Actions bearer token is unchanged")
        return
    atomic_write(paths.gpt_actions_token_file, content, 0o600)
    LOG.info("stored protected GPT Actions bearer token")


def resolve_service_tokens(
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> tuple[str, str]:
    mcp_auth_token = resolve_mcp_auth_token(paths, redactor)
    gpt_actions_token = resolve_gpt_actions_token(paths, redactor)
    if constant_time_token_equal(mcp_auth_token, gpt_actions_token):
        raise ToolError("GPT Actions and MCP bearer tokens must be different")
    return mcp_auth_token, gpt_actions_token


def validate_distinct_service_tokens(
    paths: InstallPaths,
    gpt_actions_token: str,
    mcp_auth_token: str,
) -> None:
    if constant_time_token_equal(gpt_actions_token, mcp_auth_token):
        raise ToolError("GPT Actions and MCP bearer tokens must be different")
    try:
        joplin_token = paths.token_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ToolError("Joplin API token file cannot be read") from None
    try:
        if constant_time_token_equal(gpt_actions_token, joplin_token):
            raise ToolError("GPT Actions and Joplin API tokens must be different")
        if constant_time_token_equal(mcp_auth_token, joplin_token):
            raise ToolError("MCP and Joplin API tokens must be different")
    finally:
        del joplin_token


def report_service_token_paths(paths: InstallPaths) -> None:
    LOG.info("service bearer token files (token values are not printed):")
    LOG.info("GPT Actions: %s", paths.gpt_actions_token_file)
    LOG.info("MCP: %s", paths.mcp_auth_token_file)


def load_secret_file(value: str | os.PathLike[str], label: str) -> str:
    path = absolute_path(value)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ToolError(f"could not read {label} file {path}: {type(exc).__name__}") from None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ToolError(f"{label} file must be a regular file, not a symlink: {path}")
        if metadata.st_uid != os.geteuid():
            raise ToolError(f"{label} file must be owned by the current user: {path}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ToolError(f"{label} file must not be accessible by group or others: {path}")
        if metadata.st_size > 4096:
            raise ToolError(f"{label} file is unexpectedly large: {path}")
        chunks: list[bytes] = []
        remaining = 4097
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        raise ToolError(f"could not read {label} file {path}: {type(exc).__name__}") from None
    finally:
        os.close(fd)
    if len(raw) > 4096:
        raise ToolError(f"{label} file is unexpectedly large: {path}")
    try:
        secret = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{label} file must contain UTF-8 text: {path}") from None
    secret = secret.removesuffix("\n").removesuffix("\r")
    if not secret or "\x00" in secret or "\n" in secret or "\r" in secret:
        raise ToolError(f"{label} file must contain exactly one non-empty line: {path}")
    return secret


def resolve_secret(
    secret_file: str | None,
    file_option: str,
    prompt: str,
    *,
    non_interactive: bool,
    getpass_fn: Callable[[str], str] = hidden_input,
) -> str:
    if secret_file:
        return load_secret_file(secret_file, prompt.strip(": "))
    if non_interactive:
        raise ToolError(f"required secret is missing; pass {file_option}")
    try:
        value = getpass_fn(prompt)
    except (EOFError, OSError):
        raise ToolError(f"interactive input is unavailable; pass {file_option}") from None
    if not value:
        raise ToolError(f"{prompt.strip(': ')} may not be empty")
    return value
