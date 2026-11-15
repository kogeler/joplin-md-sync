"""Joplin Terminal installation: sync."""

from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

from .cli import selected_sync_target, terminal_input
from .commands import CommandRunner
from .common import (
    SecretRedactor,
    ToolError,
    atomic_write,
    resolved_path,
)
from .credentials import hidden_input, load_secret_file, resolve_secret
from .models import InstallPaths, SyncConfiguration, SyncSetting, SyncTargetSpec

LOG = logging.getLogger("joplin-terminal-installer")


SYNC_TIMEOUT = 24 * 60 * 60.0


_SAML_CODE_RE = re.compile(r"^[0-9]{9}$")


def parse_setting(output: str, name: str) -> str:
    prefix = f"{name} ="
    for line in output.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()[len(prefix) :].strip()
    raise ToolError(f"Joplin did not return setting {name}")


def joplin_command(paths: InstallPaths, *arguments: str) -> list[str]:
    return [str(paths.launcher), "--profile", str(paths.profile_dir), *arguments]


def read_setting(
    runner: CommandRunner,
    paths: InstallPaths,
    name: str,
    *,
    sensitive: bool = False,
) -> str:
    result = runner.run(
        joplin_command(paths, "config", name),
        sensitive_output=sensitive,
    )
    return parse_setting(result.stdout, name)


def read_profile_settings(
    runner: CommandRunner,
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> dict[str, object]:
    """Use the supported CLI while the stopped profile is exclusively locked."""
    result = runner.run(joplin_command(paths, "config", "--export"), sensitive_output=True)
    try:
        if len(result.stdout) > 1024 * 1024:
            raise ValueError
        settings = json.loads(result.stdout)
        if not isinstance(settings, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise ToolError("Joplin returned invalid exported configuration") from None
    for name, value in settings.items():
        if any(
            part in name.lower() for part in ("password", "token", "auth", "username")
        ) or name in {"sync.11.id", "sync.11.userId"}:
            if isinstance(value, str):
                redactor.add(value)
    return settings


def clear_unused_sync_settings(
    runner: CommandRunner,
    paths: InstallPaths,
    current: Mapping[str, object],
    configuration: SyncConfiguration,
    *,
    reset: bool,
) -> None:
    selected = str(configuration.target.target_id)
    written = {setting.name for setting in configuration.settings}
    cleared: dict[str, str | bool] = {}
    for name, value in current.items():
        parts = name.split(".")
        if len(parts) != 3 or parts[0] != "sync" or not parts[1].isdigit():
            continue
        if parts[2] in {"id", "userId"} and parts[1] != "11":
            continue
        if parts[2] not in {
            "path",
            "username",
            "password",
            "auth",
            "url",
            "region",
            "forcePathStyle",
            "id",
            "userId",
        }:
            continue
        if (parts[1] != selected or reset) and name not in written:
            cleared[name] = False if isinstance(value, bool) else ""
    if cleared:
        runner.run(
            joplin_command(paths, "config", "--import"),
            input_text=json.dumps(cleared),
            sensitive_output=True,
        )


def write_setting(
    runner: CommandRunner,
    paths: InstallPaths,
    name: str,
    value: str,
    *,
    secret: bool = False,
) -> None:
    # Use the supported CLI import interface: even an access key or a username
    # must not become part of the process argument vector. Import takes typed
    # JSON, unlike the positional config command which converts CLI strings.
    typed_value: str | int | bool = value
    if name in ("sync.target", "sync.interval", "api.port"):
        typed_value = int(value)
    elif name.endswith(".forcePathStyle"):
        typed_value = value == "true"
    runner.run(
        joplin_command(paths, "config", "--import"),
        input_text=json.dumps({name: typed_value}),
        sensitive_output=True,
    )


def confirm_reconfigure(
    args: argparse.Namespace,
    configuration: SyncConfiguration,
    current_target: str,
    current_settings: Mapping[str, str],
    input_fn: Callable[[str], str] = terminal_input,
) -> bool:
    target_id = str(configuration.target.target_id)
    if current_target in ("0", "null", ""):
        return False
    target_mismatch = current_target != target_id
    setting_mismatch = any(
        current_settings.get(setting.name, "") not in ("", "null", setting.value)
        for setting in configuration.public_settings
    )
    if not (target_mismatch or setting_mismatch):
        return False
    LOG.warning(
        "existing profile sync configuration differs from requested %s", configuration.target.label
    )
    if args.force_reconfigure:
        return True
    explicit = getattr(args, "explicit_options", ())
    if isinstance(explicit, (set, frozenset)) and explicit & {"sync_target", "sync_location"}:
        return True
    if args.non_interactive:
        raise ToolError(
            "existing sync configuration differs; pass --force-reconfigure to change it"
        )
    answer = input_fn(f"Reconfigure this profile for {configuration.target.label}? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        raise ToolError("configuration change cancelled")
    return True


def resolve_sync_configuration(
    args: argparse.Namespace,
    redactor: SecretRedactor,
    *,
    getpass_fn: Callable[[str], str] = hidden_input,
) -> SyncConfiguration:
    target = selected_sync_target(args)
    username = args.sync_username or ""
    if target.target_id == 8 and not username:
        username = resolve_secret(
            args.sync_username_file,
            "--sync-username-file",
            "S3 access key: ",
            non_interactive=args.non_interactive,
            getpass_fn=getpass_fn,
        )
    elif args.sync_username_file:
        username = load_secret_file(args.sync_username_file, "sync username")
    redactor.add(username)
    settings: list[SyncSetting] = []
    prefix = f"sync.{target.target_id}"

    def password(label: str) -> str:
        saved = getattr(args, "saved_sync_secret", None)
        if saved and not args.sync_secret_file:
            value = str(saved)
        else:
            value = resolve_secret(
                args.sync_secret_file,
                "--sync-secret-file",
                label + ": ",
                non_interactive=args.non_interactive,
                getpass_fn=getpass_fn,
            )
        redactor.add(value)
        return value

    if target.target_id == 2:
        settings.append(SyncSetting(f"{prefix}.path", str(resolved_path(args.sync_location))))
    elif target.target_id in (5, 6, 9):
        settings.append(SyncSetting(f"{prefix}.path", args.sync_location.rstrip("/")))
        settings.append(SyncSetting(f"{prefix}.username", username))
        if target.credentials_optional and not username:
            secret = ""
        else:
            assert target.secret_label is not None
            secret = password(target.secret_label)
        settings.append(SyncSetting(f"{prefix}.password", secret, secret=True))
    elif target.target_id == 8:
        secret = password("S3 secret key")
        settings.extend(
            (
                SyncSetting(f"{prefix}.path", args.sync_location),
                SyncSetting(
                    f"{prefix}.url",
                    (args.s3_endpoint or "https://s3.amazonaws.com/").rstrip("/") + "/",
                ),
                SyncSetting(f"{prefix}.region", args.s3_region),
                SyncSetting(f"{prefix}.username", username),
                SyncSetting(f"{prefix}.password", secret, secret=True),
                SyncSetting(
                    f"{prefix}.forcePathStyle",
                    "true" if args.s3_force_path_style else "false",
                ),
            )
        )
    elif target.target_id == 11:
        settings.append(SyncSetting(f"{prefix}.path", args.sync_location.rstrip("/")))
    return SyncConfiguration(target, tuple(settings))


def configure_profile(
    runner: CommandRunner,
    paths: InstallPaths,
    args: argparse.Namespace,
    configuration: SyncConfiguration,
    *,
    input_fn: Callable[[str], str] = terminal_input,
) -> bool:
    paths.profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.profile_dir.chmod(0o700)
    current_target = read_setting(runner, paths, "sync.target").split(maxsplit=1)[0]
    current_settings = {
        setting.name: read_setting(runner, paths, setting.name)
        for setting in configuration.public_settings
    }
    reconfigured = confirm_reconfigure(
        args,
        configuration,
        current_target,
        current_settings,
        input_fn,
    )
    LOG.info(
        "configuring %s target %d and recurrent sync",
        configuration.target.label,
        configuration.target.target_id,
    )
    for setting in configuration.settings:
        write_setting(
            runner,
            paths,
            setting.name,
            setting.value,
            secret=setting.secret,
        )
    write_setting(runner, paths, "sync.target", str(configuration.target.target_id))
    write_setting(runner, paths, "sync.interval", str(args.sync_interval))
    write_setting(runner, paths, "api.port", str(args.api_port))
    return reconfigured


def run_sync(runner: CommandRunner, paths: InstallPaths, label: str) -> None:
    LOG.info("%s", label)
    # Joplin can return zero even when its synchronizer reports an error. Its
    # CLI has no structured sync result, so use a known report language only
    # for this command and restore the operator's locale even on failure.
    locale = read_setting(runner, paths, "locale").split(" ", 1)[0]
    if locale != "en_GB":
        write_setting(runner, paths, "locale", "en_GB")
    try:
        result = runner.run_long(
            joplin_command(paths, "sync"),
            heartbeat_label=label,
            timeout=SYNC_TIMEOUT,
            sensitive_output=True,
        )
        report = result.stdout + "\n" + result.stderr
        if (
            "Completed:" not in report
            or "Last error:" in report
            or "Sync target must be upgraded" in report
        ):
            raise ToolError(f"{label} did not complete successfully; service startup is refused")
    finally:
        if locale != "en_GB":
            write_setting(runner, paths, "locale", locale)


def run_browser_authenticated_sync(
    runner: CommandRunner,
    paths: InstallPaths,
    target: SyncTargetSpec,
) -> None:
    LOG.info("starting interactive %s authentication and initial sync", target.label)
    if target.target_id == 3:
        LOG.info("OneDrive redirects to 127.0.0.1:9967; forward that port when the host is remote")
    runner.run_interactive(joplin_command(paths, "sync"), timeout=SYNC_TIMEOUT)
    assert target.auth_setting is not None
    persisted_auth = read_setting(runner, paths, target.auth_setting, sensitive=True)
    if persisted_auth in ("", "null"):
        raise ToolError(f"{target.label} authentication was not completed")
    del persisted_auth
    run_sync(
        runner,
        paths,
        f"verifying persisted {target.label} authentication",
    )


def exchange_saml_code(
    server_url: str,
    code: str,
    *,
    timeout: float = 30.0,
    urlopen: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, str]:
    cleaned = re.sub(r"[\s-]", "", code)
    if not _SAML_CODE_RE.fullmatch(cleaned):
        raise ToolError("Joplin Server SAML login code must contain exactly 9 digits")
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/api/login_with_code/{cleaned}",
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # type: ignore[attr-defined]
            raw = response.read(64 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        exc.read(64 * 1024 + 1)
        status = exc.code
        exc.close()
        raise ToolError(f"Joplin Server rejected the SAML login code (HTTP {status})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"Joplin Server SAML login failed: {type(exc).__name__}") from None
    if len(raw) > 64 * 1024:
        raise ToolError("Joplin Server SAML login response is unexpectedly large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError("Joplin Server returned an invalid SAML login response") from None
    if not isinstance(payload, dict):
        raise ToolError("Joplin Server returned an invalid SAML login response")
    session_id = payload.get("id")
    user_id = payload.get("user_id")
    for name, value in (("id", session_id), ("user_id", user_id)):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(character.isspace() or ord(character) < 0x20 for character in value)
        ):
            raise ToolError(f"Joplin Server SAML response has an invalid {name}")
    assert isinstance(session_id, str)
    assert isinstance(user_id, str)
    return session_id, user_id


def authenticate_saml(
    runner: CommandRunner,
    paths: InstallPaths,
    configuration: SyncConfiguration,
    redactor: SecretRedactor,
    *,
    reuse_existing: bool,
    input_fn: Callable[[str], str] = terminal_input,
    getpass_fn: Callable[[str], str] = hidden_input,
) -> None:
    if reuse_existing:
        session_id = read_setting(runner, paths, "sync.11.id", sensitive=True)
        user_id = read_setting(runner, paths, "sync.11.userId", sensitive=True)
        if session_id not in ("", "null") and user_id not in ("", "null"):
            del session_id
            del user_id
            LOG.info("reusing existing Joplin Server SAML session")
            return
    server_url = next(
        setting.value for setting in configuration.settings if setting.name == "sync.11.path"
    )
    LOG.info(
        "open this URL in a browser and complete SAML login: %s/login/sso-saml-app", server_url
    )
    code = getpass_fn("Joplin Server SAML login code: ")
    redactor.add(code)
    session_id, user_id = exchange_saml_code(server_url, code)
    redactor.add(session_id)
    redactor.add(user_id)
    write_setting(runner, paths, "sync.11.id", session_id, secret=True)
    write_setting(runner, paths, "sync.11.userId", user_id, secret=True)
    LOG.info("Joplin Server SAML authentication completed")


def extract_api_token(
    runner: CommandRunner,
    paths: InstallPaths,
    redactor: SecretRedactor,
) -> None:
    token = read_setting(runner, paths, "api.token", sensitive=True)
    if token in ("", "null") or len(token) < 32 or any(character.isspace() for character in token):
        raise ToolError("Joplin did not provide a valid api.token")
    redactor.add(token)
    atomic_write(paths.token_file, f"{token}\n".encode(), 0o600)
    del token
    LOG.info("protected Joplin API token file: %s", paths.token_file)
