"""Joplin Terminal installation: encryption."""

from __future__ import annotations

import argparse
import errno
import logging
import os
import pty
import re
import selectors
import signal
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .commands import (
    LONG_OPERATION_HEARTBEAT,
    MAX_LONG_OUTPUT_BYTES,
    CommandRunner,
    _append_bounded,
    _format_elapsed,
)
from .common import (
    SecretRedactor,
    ToolError,
    child_environment,
    safe_command,
)
from .credentials import hidden_input, resolve_secret
from .models import E2eeResult, InstallPaths
from .sync import joplin_command

LOG = logging.getLogger("joplin-terminal-installer")


E2EE_TIMEOUT = 24 * 60 * 60.0


_PASSWORD_PROMPT_RE = re.compile(rb"(?i)(?:master[ -]?)?password[^\r\n]{0,80}:")


def _terminate_pty_child(pid: int, timeout: float = 5.0) -> int:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _waited, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def run_e2ee_decrypt_pty(
    joplin: Path,
    profile_dir: Path,
    password: str,
    *,
    timeout: float = E2EE_TIMEOUT,
    heartbeat_interval: float = LONG_OPERATION_HEARTBEAT,
    max_output_bytes: int = MAX_LONG_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> E2eeResult:
    if heartbeat_interval <= 0 or max_output_bytes <= 0:
        raise ValueError("heartbeat interval and output limit must be positive")
    child_env = child_environment(env)
    child_env.update({"TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24"})
    pid, fd = pty.fork()
    if pid == 0:
        argv = [
            str(joplin),
            "--profile",
            str(profile_dir),
            "e2ee",
            "decrypt",
            "--retry-failed-items",
        ]
        os.execve(str(joplin), argv, child_env)
    os.set_blocking(fd, False)
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    output = bytearray()
    password_bytes = bytearray(password.encode("utf-8"))
    prompt_scan_start = 0
    prompts = 0
    started = time.monotonic()
    deadline = started + timeout
    next_heartbeat = started + heartbeat_interval
    exit_code: int | None = None

    def redacted_output() -> bytes:
        return bytes(output).replace(bytes(password_bytes), b"[REDACTED]")

    try:
        while time.monotonic() < deadline:
            for _key, _mask in selector.select(0.1):
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    chunk = chunk.replace(bytes(password_bytes), b"[REDACTED]")
                    dropped = _append_bounded(output, chunk, max_output_bytes)
                    prompt_scan_start = max(0, prompt_scan_start - dropped)
            match = _PASSWORD_PROMPT_RE.search(output, prompt_scan_start)
            if match:
                os.write(fd, password_bytes + b"\r")
                prompts += 1
                prompt_scan_start = match.end()
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                exit_code = os.waitstatus_to_exitcode(status)
                break
            now = time.monotonic()
            if now >= next_heartbeat:
                LOG.info(
                    "E2EE decryption still running (elapsed %s)",
                    _format_elapsed(now - started),
                )
                while next_heartbeat <= now:
                    next_heartbeat += heartbeat_interval
        if exit_code is None:
            _terminate_pty_child(pid)
            return E2eeResult(124, redacted_output(), prompts, timed_out=True)
        return E2eeResult(exit_code, redacted_output(), prompts)
    finally:
        for index in range(len(password_bytes)):
            password_bytes[index] = 0
        os.close(fd)


def e2ee_status_enabled(runner: CommandRunner, paths: InstallPaths) -> bool:
    result = runner.run(joplin_command(paths, "e2ee", "status"), check=False)
    if result.returncode != 0:
        raise ToolError(f"Joplin E2EE status failed with exit {result.returncode}")
    if "Encryption is: Enabled" in result.stdout:
        return True
    if "Encryption is: Disabled" in result.stdout:
        return False
    raise ToolError("Joplin returned an unrecognised E2EE status")


def persisted_e2ee_works(runner: CommandRunner, paths: InstallPaths) -> bool:
    result = runner.run_long(
        joplin_command(
            paths,
            "e2ee",
            "decrypt",
            "--retry-failed-items",
            "--force",
        ),
        heartbeat_label="E2EE persistence verification",
        check=False,
        timeout=E2EE_TIMEOUT,
        sensitive_output=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "Completed decryption." in output


def manual_e2ee_error(paths: InstallPaths, detail: str) -> ToolError:
    command = safe_command(joplin_command(paths, "e2ee", "decrypt", "--retry-failed-items"))
    return ToolError(f"{detail}. Run this command manually: {command}; then rerun the installer")


def bootstrap_e2ee(
    runner: CommandRunner,
    paths: InstallPaths,
    args: argparse.Namespace,
    redactor: SecretRedactor,
    *,
    getpass_fn: Callable[[str], str] = hidden_input,
) -> bool:
    if not e2ee_status_enabled(runner, paths):
        runner.run(joplin_command(paths, "status"), sensitive_output=True)
        LOG.info("remote data does not use Joplin E2EE; no key bootstrap is needed")
        return False
    if persisted_e2ee_works(runner, paths):
        LOG.info("existing E2EE key cache is valid; bootstrap is not repeated")
    else:
        password = resolve_secret(
            args.e2ee_password_file,
            "--e2ee-password-file",
            "Joplin E2EE password: ",
            non_interactive=args.non_interactive,
            getpass_fn=getpass_fn,
        )
        redactor.add(password)
        LOG.info("unlocking existing E2EE master keys")
        result = run_e2ee_decrypt_pty(paths.launcher, paths.profile_dir, password)
        del password
        lowered = result.output.lower()
        if result.timed_out:
            raise manual_e2ee_error(paths, "E2EE decryption timed out")
        if b"invalid password" in lowered:
            raise ToolError("Joplin rejected the E2EE password")
        if result.exit_code != 0:
            raise manual_e2ee_error(
                paths, f"E2EE decrypt command failed with exit {result.exit_code}"
            )
        if result.prompts < 1:
            raise manual_e2ee_error(paths, "Joplin did not present a recognised password prompt")
        if b"completed decryption." not in lowered:
            raise manual_e2ee_error(paths, "Joplin did not confirm completed decryption")
        if not persisted_e2ee_works(runner, paths):
            raise ToolError("E2EE password did not remain usable in a fresh Joplin process")
        LOG.info("E2EE keys unlocked and encrypted items decrypted")

    # The command output contains notebook names, so it is captured and never logged.
    runner.run(joplin_command(paths, "status"), sensitive_output=True)
    LOG.info("Joplin can read synced metadata without decryption errors")
    return True
