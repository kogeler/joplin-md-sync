"""Shared bearer-token validation and protected file handling."""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import re
import stat
from pathlib import Path

MIN_BEARER_TOKEN_BYTES = 32
MAX_BEARER_TOKEN_CHARS = 1024
MAX_TOKEN_FILE_BYTES = MAX_BEARER_TOKEN_CHARS + 2
MAX_AUTHORIZATION_CHARS = len("Bearer ") + MAX_BEARER_TOKEN_CHARS

_URLSAFE_BASE64_RE = re.compile(r"[A-Za-z0-9_-]+={0,2}")


class BearerTokenError(ValueError):
    """A token or token file does not meet the authentication boundary."""


def validate_bearer_token(token: str, *, label: str) -> str:
    """Require a bounded URL-safe Base64 token encoding at least 256 bits."""
    if not token or len(token) > MAX_BEARER_TOKEN_CHARS:
        raise BearerTokenError(
            f"{label} token must contain at most {MAX_BEARER_TOKEN_CHARS} characters"
        )
    if _URLSAFE_BASE64_RE.fullmatch(token) is None:
        raise BearerTokenError(f"{label} token must use URL-safe Base64 encoding")
    try:
        encoded = token.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise BearerTokenError(f"{label} token must use URL-safe Base64 encoding") from None
    if len(decoded) < MIN_BEARER_TOKEN_BYTES:
        raise BearerTokenError(
            f"{label} token must encode at least {MIN_BEARER_TOKEN_BYTES} bytes"
        )
    return token


def read_protected_bearer_token(path: Path, *, label: str) -> str:
    """Read one bounded token line without following or racing a symlink."""
    descriptor = -1
    try:
        path_info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(path_info.st_mode):
            raise BearerTokenError(f"{label} token file must be a regular file: {path}")

        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise BearerTokenError(f"{label} token file must be a regular file: {path}")
        if (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino):
            raise BearerTokenError(f"{label} token file changed while it was being opened")
        if os.name == "posix":
            if info.st_mode & 0o077:
                raise BearerTokenError(
                    f"{label} token file must not be accessible by group or others: {path}"
                )
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise BearerTokenError(
                    f"{label} token file must be owned by the current user: {path}"
                )
        if info.st_size > MAX_TOKEN_FILE_BYTES:
            raise BearerTokenError(f"{label} token file is too large: {path}")

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_TOKEN_FILE_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_TOKEN_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_TOKEN_FILE_BYTES:
            raise BearerTokenError(f"{label} token file is too large: {path}")
        raw = b"".join(chunks)
    except BearerTokenError:
        raise
    except OSError as exc:
        raise BearerTokenError(f"{label} token file cannot be read: {path}: {exc}") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if b"\r" in raw or b"\n" in raw:
        raise BearerTokenError(f"{label} token file must contain exactly one line: {path}")
    try:
        token = raw.decode("ascii")
    except UnicodeDecodeError:
        raise BearerTokenError(f"{label} token must use URL-safe Base64 encoding") from None
    if not token:
        raise BearerTokenError(f"{label} token file is empty: {path}")
    return validate_bearer_token(token, label=label)


def _bearer_token_bytes(authorization: str | None) -> bytes | None:
    if authorization is None or len(authorization) > MAX_AUTHORIZATION_CHARS:
        return None
    scheme, separator, supplied = authorization.partition(" ")
    if separator != " " or scheme != "Bearer" or not supplied:
        return None
    if supplied != supplied.strip():
        return None
    try:
        return supplied.encode("ascii")
    except UnicodeEncodeError:
        return None


def bearer_token_syntax_valid(authorization: str | None) -> bool:
    """Return whether a header is one bounded, ASCII Bearer credential."""
    return _bearer_token_bytes(authorization) is not None


def accepts_bearer_token(authorization: str | None, expected: str) -> bool:
    """Strictly parse and constant-time compare one Bearer credential."""
    supplied_bytes = _bearer_token_bytes(authorization)
    if supplied_bytes is None:
        return False
    try:
        expected_bytes = expected.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied_bytes, expected_bytes)


def token_values_equal(left: str, right: str) -> bool:
    """Compare two ASCII token values without raising on malformed text."""
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeEncodeError:
        return False
