"""Joplin Terminal installation: prompts."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .cli import selected_sync_target
from .common import resolved_path
from .credentials import load_secret_file
from .models import SYNC_TARGETS, InstallPaths

LOG = logging.getLogger("joplin-terminal-installer")


class SetupPrompts:
    """CLI/environment answers and terminal answers configure the same fields."""

    def __init__(self, args: argparse.Namespace, input_fn: Callable[[str], str]) -> None:
        self.args = args
        self.input = input_fn

    def value(
        self,
        name: str,
        label: str,
        *,
        default: object = None,
        required: bool = False,
        choices: Sequence[str] = (),
        integer: bool = False,
    ) -> None:
        if name in self.args.explicit_options:
            return
        if self.args.non_interactive:
            if default is not None:
                setattr(self.args, name, default)
            return
        while True:
            suffix = f" [{default}]" if default is not None else ""
            raw = self.input(label + suffix + ": ").strip()
            value = raw if raw else default
            if required and value in (None, ""):
                LOG.warning("%s is required", label)
                continue
            if choices and value not in choices:
                LOG.warning("choose one of: %s", ", ".join(choices))
                continue
            if integer:
                try:
                    value = int(str(value))
                except ValueError:
                    LOG.warning("%s must be an integer", label)
                    continue
            setattr(self.args, name, value)
            self.args.explicit_options.add(name)
            return

    def toggle(self, name: str, label: str, *, inverted: bool = False) -> None:
        if name in self.args.explicit_options:
            return
        if self.args.non_interactive:
            setattr(self.args, name, inverted)
            return
        while True:
            answer = self.input(label + " [y/N] ").strip().lower()
            if answer in ("", "n", "no", "y", "yes"):
                enabled = answer in ("y", "yes")
                setattr(self.args, name, not enabled if inverted else enabled)
                self.args.explicit_options.add(name)
                return
            LOG.warning("answer yes or no")


def existing_profile(profile_dir: Path) -> bool:
    """Detect a non-empty profile directory without opening any Joplin file."""
    try:
        return profile_dir.is_dir() and next(profile_dir.iterdir(), None) is not None
    except OSError:
        # An unreadable directory is not treated as empty; Joplin reports it.
        return True


def complete_sync_options(
    args: argparse.Namespace,
    prompts: SetupPrompts,
    current: Mapping[str, object] | None = None,
    *,
    ask_unconfigured: bool = False,
) -> None:
    current = {} if current is None or args.force_reconfigure else current
    current_id = str(current.get("sync.target", ""))
    current_target = next(
        (name for name, target in SYNC_TARGETS.items() if str(target.target_id) == current_id),
        None,
    )
    if (
        not args.sync_target
        and current_target
        and not (ask_unconfigured and current_target == "none")
    ):
        args.sync_target = current_target
    if not args.sync_target:
        prompts.value(
            "sync_target",
            "Sync backend (" + "/".join(SYNC_TARGETS) + ")",
            default="none",
            choices=tuple(SYNC_TARGETS),
        )
    target = selected_sync_target(args)
    prefix = f"sync.{target.target_id}"
    same_target = args.sync_target == current_target
    if (
        same_target
        and target.username_label
        and not args.sync_username
        and not args.sync_username_file
    ):
        saved_username = current.get(prefix + ".username")
        if saved_username:
            args.sync_username = str(saved_username)
    fields = [("sync_location", target.location_label, "path")]
    if target.target_id != 8 and not args.sync_username_file:
        fields.append(("sync_username", target.username_label, "username"))
    if target.target_id == 8:
        fields += [("s3_endpoint", "S3 endpoint", "url"), ("s3_region", "S3 region", "region")]
    for name, label, setting in fields:
        if not label or getattr(args, name) is not None:
            continue
        saved = current.get(f"{prefix}.{setting}") if same_target else None
        if saved not in (None, "", "null"):
            setattr(args, name, str(saved))
        else:
            prompts.value(
                name,
                label,
                default="https://s3.amazonaws.com/" if name == "s3_endpoint" else None,
                required=name != "sync_username" or not target.credentials_optional,
            )
    if target.target_id == 8 and args.s3_force_path_style is None:
        saved = current.get(prefix + ".forcePathStyle") if same_target else None
        if saved is not None:
            args.s3_force_path_style = saved is True or saved == "true"
        else:
            prompts.toggle("s3_force_path_style", "Use S3 path-style requests?")
    identity = {}
    if target.location_label:
        identity["path"] = args.sync_location
    if target.username_label:
        identity["username"] = args.sync_username or ""
    if args.sync_username_file:
        identity["username"] = load_secret_file(args.sync_username_file, "sync username")
    if target.target_id == 8:
        identity.update(url=args.s3_endpoint, region=args.s3_region)

    def comparable(key: str, value: object) -> str:
        text = str(value or "")
        # Match only normalization actually applied by resolve_sync_configuration.
        # Account names, regions and bucket names must retain their exact identity.
        if key == "url" or (key == "path" and target.target_id in (5, 6, 9, 11)):
            return text.rstrip("/")
        return text

    unchanged = same_target and all(
        comparable(key, value) == comparable(key, current.get(prefix + "." + key))
        for key, value in identity.items()
    )
    args.saved_sync_secret = current.get(prefix + ".password") if unchanged else None
    browser_auth = current.get(target.auth_setting) if target.auth_setting else None
    if target.saml_auth:
        browser_auth = current.get("sync.11.id") and current.get("sync.11.userId")
    args.saved_browser_auth = unchanged and bool(browser_auth)


def complete_setup_options(
    args: argparse.Namespace,
    paths: InstallPaths,
    *,
    installed: bool,
    input_fn: Callable[[str], str],
) -> None:
    if args.dry_run:
        if not installed or args.force_reconfigure:
            for name in ("no_enable_service", "no_start_service"):
                if name not in args.explicit_options:
                    setattr(args, name, True)
        return
    prompts = SetupPrompts(args, input_fn)
    if not installed or args.force_reconfigure:
        for name, label in (
            ("joplin_prefix", "Installation prefix"),
            ("profile_dir", "Joplin profile directory"),
            ("joplin_version", "Joplin version (or latest)"),
            ("joplin_md_sync_version", "joplin-md-sync version (or latest)"),
            ("api_port", "Local Joplin API port"),
            ("mcp_port", "MCP/Actions port"),
            ("sync_interval", "Sync interval in seconds"),
        ):
            prompts.value(
                name,
                label,
                default=getattr(args, name),
                integer=name
                in {
                    "api_port",
                    "mcp_port",
                    "sync_interval",
                },
            )
        # Interrupted setup can leave a profile without units. Its settings are
        # readable only through the Joplin CLI under the profile lock, so sync
        # questions wait until then and use the configured values as defaults.
        args.deferred_sync = (
            not installed
            and not args.force_reconfigure
            and existing_profile(resolved_path(args.profile_dir))
        )
        if not args.deferred_sync:
            complete_sync_options(args, prompts)
        prompts.toggle("allow_remote_mcp", "Allow remote MCP/Actions connections?")
        if not args.no_github_oauth:
            prompts.toggle("github_oauth", "Enable GitHub OAuth for Claude?")
        prompts.toggle("enable_linger", "Enable user services after logout?")
        prompts.toggle("no_enable_service", "Enable automatic service startup?", inverted=True)
        prompts.toggle("no_start_service", "Start services after setup?", inverted=True)
