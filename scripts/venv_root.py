#!/usr/bin/env python3
"""Print the machine- and user-scoped root of checkout-local Make environments.

One checkout on a shared or network drive can be used from several hosts. A
virtual environment contains host-specific interpreter links and binaries, so
every OS machine identity and local user receives a private directory under
``.venvs/``. The raw machine identity is never printed. There is deliberately
no shared fallback: without a valid identity Make stops instead of reusing
another host's environment.

Make runs this file directly with ``python -I``; it imports only the standard
library so it works before any environment is prepared.
"""

from __future__ import annotations

import getpass
import hmac
import os
import re
import sys
from pathlib import Path, PurePosixPath

MACHINE_ID_FILE = Path("/etc/machine-id")
WINDOWS_MACHINE_GUID_KEY = r"SOFTWARE\Microsoft\Cryptography"
WINDOWS_MACHINE_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NAMESPACE = b"joplin-md-sync/venv-namespace/v1"
VENV_PARENT = ".venvs"
# 64 bits keeps distinct hosts apart while leaving room for deep site-packages
# paths below the Windows MAX_PATH limit.
KEY_HEX_LENGTH = 16


class MachineIdentityError(RuntimeError):
    """No valid OS identity is available for selecting a private environment."""


def linux_machine_id() -> bytes:
    """Return the systemd machine ID as bytes after strict validation."""
    try:
        with MACHINE_ID_FILE.open("rb") as stream:
            value = stream.read(34)
    except OSError as error:
        raise MachineIdentityError(
            "cannot read /etc/machine-id; a valid OS machine ID is required"
        ) from error
    if re.fullmatch(rb"[0-9a-f]{32}\n?", value) is None or int(value, 16) == 0:
        raise MachineIdentityError(
            "/etc/machine-id is invalid or uninitialized; no shared venv fallback is allowed"
        )
    return bytes.fromhex(value.decode("ascii").strip())


def parse_windows_machine_guid(value: object) -> bytes:
    """Validate a registry MachineGuid string and return its raw bytes."""
    guid = value.casefold() if isinstance(value, str) else ""
    if WINDOWS_MACHINE_GUID.fullmatch(guid) is None or int(guid.replace("-", ""), 16) == 0:
        raise MachineIdentityError(
            "the Windows MachineGuid is invalid or uninitialized; "
            "no shared venv fallback is allowed"
        )
    return bytes.fromhex(guid.replace("-", ""))


def windows_machine_guid() -> bytes:
    """Read the 64-bit registry view of the Windows installation identity."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            WINDOWS_MACHINE_GUID_KEY,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, "MachineGuid")
    except OSError as error:
        raise MachineIdentityError(
            "cannot read the Windows MachineGuid; a valid OS machine ID is required"
        ) from error
    if value_type != winreg.REG_SZ:
        value = None
    return parse_windows_machine_guid(value)


def machine_identity(platform: str = sys.platform) -> bytes:
    """Return the validated identity of the current OS installation."""
    if platform == "win32":
        return windows_machine_guid()
    return linux_machine_id()


def user_identity(platform: str = sys.platform) -> bytes:
    """Return the local account identity: the UID, or the Windows user name."""
    if platform == "win32":
        try:
            return getpass.getuser().casefold().encode("utf-8")
        except OSError as error:
            raise MachineIdentityError("cannot determine the local Windows user") from error
    return str(os.getuid()).encode("ascii")


def environment_key() -> str:
    """Derive an application-specific key without publishing the raw machine ID."""
    digest = hmac.digest(NAMESPACE, machine_identity() + b"\0" + user_identity(), "sha256")
    return digest.hex()[:KEY_HEX_LENGTH]


def repository_venv_root() -> PurePosixPath:
    """Return the checkout-relative root shared by every Make environment."""
    return PurePosixPath(VENV_PARENT, environment_key())


def main() -> int:
    try:
        print(repository_venv_root())
    except MachineIdentityError as error:
        print(f"venv_root: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
