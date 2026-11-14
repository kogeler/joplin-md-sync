"""Private nonsecret installer choices that systemd units do not describe."""

from __future__ import annotations

import argparse
import json

from .common import ToolError, atomic_write
from .credentials import load_secret_file
from .models import InstallPaths

POLICY_FIELDS = {
    "no_enable_service": bool,
    "no_start_service": bool,
    "enable_linger": bool,
    "joplin_version": str,
    "joplin_md_sync_version": str,
}


def restore_installer_choices(paths: InstallPaths, args: argparse.Namespace) -> None:
    path = paths.mcp_config_dir / "installer-settings.json"
    if args.force_reconfigure or not path.exists():
        return
    try:
        document = json.loads(load_secret_file(path, "installer settings"))
        choices = document["choices"]
        if (
            document["schema"] != 1
            or not isinstance(choices, dict)
            or choices.keys() - POLICY_FIELDS.keys()
        ):
            raise ValueError
        for name, value in choices.items():
            if type(value) is not POLICY_FIELDS[name]:
                raise ValueError
            if name not in args.explicit_options:
                setattr(args, name, value)
    except (ValueError, TypeError, KeyError):
        raise ToolError(
            "invalid saved installer choices; use --reconfigure to replace them"
        ) from None


def save_installer_choices(paths: InstallPaths, args: argparse.Namespace) -> None:
    # Unanswered optional choices (e.g. lingering during an update) stay unset
    # instead of storing null, which a later restore would reject.
    choices = {name: value for name in POLICY_FIELDS if (value := getattr(args, name)) is not None}
    atomic_write(
        paths.mcp_config_dir / "installer-settings.json",
        json.dumps({"schema": 1, "choices": choices}).encode() + b"\n",
        0o600,
    )
