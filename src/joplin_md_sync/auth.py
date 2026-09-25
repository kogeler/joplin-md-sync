"""Shared bearer-token validation and protected file handling."""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import re
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MIN_BEARER_TOKEN_BYTES = 32
MAX_BEARER_TOKEN_CHARS = 1024
MAX_TOKEN_FILE_BYTES = MAX_BEARER_TOKEN_CHARS + 2
MAX_AUTHORIZATION_CHARS = len("Bearer ") + MAX_BEARER_TOKEN_CHARS

_URLSAFE_BASE64_RE = re.compile(r"[A-Za-z0-9_-]+={0,2}")


class BearerTokenError(ValueError):
    """A token or token file does not meet the authentication boundary."""


class ProtectedFileError(ValueError):
    """A secret file does not meet the protected local file boundary."""


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
        raise BearerTokenError(f"{label} token must encode at least {MIN_BEARER_TOKEN_BYTES} bytes")
    return token


# Windows access control. The decision is a pure function so every platform can
# test it; only the WinAPI calls that collect an open file's ACL are Windows-only.
WINDOWS_SYSTEM_SID = "S-1-5-18"
WINDOWS_ADMINISTRATORS_SID = "S-1-5-32-544"
# OWNER RIGHTS stands for the file owner, who is already required to be trusted.
# Python creates private temporary directories with exactly these three SIDs.
WINDOWS_OWNER_RIGHTS_SID = "S-1-3-4"
_WINDOWS_TRUSTED_SIDS = frozenset(
    {WINDOWS_SYSTEM_SID, WINDOWS_ADMINISTRATORS_SID, WINDOWS_OWNER_RIGHTS_SID}
)
_WINDOWS_ALLOW_ACE_TYPES = frozenset({0x00, 0x09})  # ACCESS_ALLOWED[_CALLBACK]_ACE_TYPE
_WINDOWS_DENY_ACE_TYPES = frozenset({0x01, 0x0A})  # ACCESS_DENIED[_CALLBACK]_ACE_TYPE
_WINDOWS_INHERIT_ONLY_ACE = 0x08
# Rights that let another account read or change the secret or its permissions.
WINDOWS_SENSITIVE_RIGHTS = (
    0x0000_0001  # FILE_READ_DATA
    | 0x0000_0002  # FILE_WRITE_DATA
    | 0x0000_0004  # FILE_APPEND_DATA
    | 0x0004_0000  # WRITE_DAC
    | 0x0008_0000  # WRITE_OWNER
    | 0x1000_0000  # GENERIC_ALL
    | 0x4000_0000  # GENERIC_WRITE
    | 0x8000_0000  # GENERIC_READ
)


@dataclass(frozen=True)
class WindowsAce:
    """One DACL entry; ``sid`` is None for entry layouts without a plain SID."""

    ace_type: int
    flags: int
    mask: int
    sid: str | None


def windows_acl_problem(owner: str, dacl: Sequence[WindowsAce] | None, user: str) -> str | None:
    """Return ``"owner"`` or ``"access"`` when a Windows file is not private.

    The owner must be the current user, SYSTEM, or Administrators. No
    effective allow entry may give any other account read, write, or
    permission-change rights. A NULL DACL grants everyone full access, and an
    allow entry that cannot be evaluated fails closed.
    """
    trusted = _WINDOWS_TRUSTED_SIDS | {user}
    if owner not in trusted or owner == WINDOWS_OWNER_RIGHTS_SID:
        return "owner"
    if dacl is None:
        return "access"
    for ace in dacl:
        if ace.flags & _WINDOWS_INHERIT_ONLY_ACE or ace.ace_type in _WINDOWS_DENY_ACE_TYPES:
            continue
        if ace.ace_type not in _WINDOWS_ALLOW_ACE_TYPES or ace.sid is None:
            return "access"
        if ace.sid not in trusted and ace.mask & WINDOWS_SENSITIVE_RIGHTS:
            return "access"
    return None


if sys.platform == "win32":  # pragma: no cover - exercised by the Windows CI jobs
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER_CLASS = 1

    class _AclHeader(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", ctypes.c_ushort),
            ("AceCount", ctypes.c_ushort),
            ("Sbz2", ctypes.c_ushort),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    _advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetSecurityInfo.restype = wintypes.DWORD
    _advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _sid_string(sid: int | None) -> str:
        if not sid:
            raise OSError("security descriptor has no owner or entry SID")
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return str(text.value)
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def _current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER_CLASS, buffer, size, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first member is the PSID.
            return _sid_string(ctypes.c_void_p.from_buffer(buffer).value)
        finally:
            _kernel32.CloseHandle(token)

    def _windows_file_acl_problem(descriptor: int) -> str | None:
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()
        status = _advapi32.GetSecurityInfo(
            msvcrt.get_osfhandle(descriptor),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if status != 0:
            raise ctypes.WinError(status)
        try:
            entries: list[WindowsAce] | None = None
            if dacl.value:
                entries = []
                header_size = ctypes.sizeof(_AceHeader)
                for index in range(_AclHeader.from_address(dacl.value).AceCount):
                    entry = ctypes.c_void_p()
                    if not _advapi32.GetAce(dacl, index, ctypes.byref(entry)) or not entry.value:
                        raise ctypes.WinError(ctypes.get_last_error())
                    header = _AceHeader.from_address(entry.value)
                    mask = 0
                    sid: str | None = None
                    if header.AceType in _WINDOWS_ALLOW_ACE_TYPES | _WINDOWS_DENY_ACE_TYPES:
                        # ACCESS_{ALLOWED,DENIED}[_CALLBACK]_ACE: header, mask, SID.
                        mask = wintypes.DWORD.from_address(entry.value + header_size).value
                        sid = _sid_string(entry.value + header_size + ctypes.sizeof(wintypes.DWORD))
                    entries.append(WindowsAce(header.AceType, header.AceFlags, mask, sid))
            return windows_acl_problem(_sid_string(owner.value), entries, _current_user_sid())
        finally:
            _kernel32.LocalFree(security_descriptor)

else:

    def _windows_file_acl_problem(descriptor: int) -> str | None:  # pragma: no cover
        raise OSError("Windows access control lists are unavailable on this platform")


def read_protected_token_line(path: Path, *, label: str) -> bytes:
    """Read one bounded, non-empty token line from a protected regular file.

    The file is opened without following or racing a symlink. On POSIX it must
    belong to the current user and grant nothing to group or others; on
    Windows its ACL must pass ``windows_acl_problem``. Only the single trailing
    line break is removed; the caller decodes and validates.
    """
    descriptor = -1
    try:
        path_info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(path_info.st_mode):
            raise ProtectedFileError(f"{label} token file must be a regular file: {path}")

        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ProtectedFileError(f"{label} token file must be a regular file: {path}")
        if (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino):
            raise ProtectedFileError(f"{label} token file changed while it was being opened")
        if os.name == "posix":
            if info.st_mode & 0o077:
                raise ProtectedFileError(
                    f"{label} token file must not be accessible by group or others: {path}"
                )
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise ProtectedFileError(
                    f"{label} token file must be owned by the current user: {path}"
                )
        elif sys.platform == "win32":  # pragma: no cover - exercised by the Windows CI jobs
            problem = _windows_file_acl_problem(descriptor)
            if problem == "owner":
                raise ProtectedFileError(
                    f"{label} token file must be owned by the current user: {path}"
                )
            if problem is not None:
                raise ProtectedFileError(
                    f"{label} token file must not be accessible by other accounts: {path}"
                )
        if info.st_size > MAX_TOKEN_FILE_BYTES:
            raise ProtectedFileError(f"{label} token file is too large: {path}")

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_TOKEN_FILE_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_TOKEN_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_TOKEN_FILE_BYTES:
            raise ProtectedFileError(f"{label} token file is too large: {path}")
        raw = b"".join(chunks)
    except ProtectedFileError:
        raise
    except OSError as exc:
        raise ProtectedFileError(f"{label} token file cannot be read: {path}: {exc}") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if b"\r" in raw or b"\n" in raw:
        raise ProtectedFileError(f"{label} token file must contain exactly one line: {path}")
    if not raw:
        raise ProtectedFileError(f"{label} token file is empty: {path}")
    return raw


def read_protected_bearer_token(path: Path, *, label: str) -> str:
    """Read one bounded bearer token line without following or racing a symlink."""
    try:
        raw = read_protected_token_line(path, label=label)
    except ProtectedFileError as exc:
        raise BearerTokenError(str(exc)) from None
    try:
        token = raw.decode("ascii")
    except UnicodeDecodeError:
        raise BearerTokenError(f"{label} token must use URL-safe Base64 encoding") from None
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
