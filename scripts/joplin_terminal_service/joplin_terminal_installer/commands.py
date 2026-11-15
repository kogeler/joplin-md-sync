"""Joplin Terminal installation: commands."""

from __future__ import annotations

import logging
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

from .common import (
    SecretRedactor,
    ToolError,
    absolute_path,
    child_environment,
    safe_command,
)
from .models import Dependencies

LOG = logging.getLogger("joplin-terminal-installer")


MINIMUM_NODE_MAJOR = 12


RECOMMENDED_NODE_MAJOR = 18


RECOMMENDED_PYTHON = (3, 14)


COMMAND_TIMEOUT = 120.0


LONG_OPERATION_HEARTBEAT = 60.0


MAX_LONG_OUTPUT_BYTES = 256 * 1024


def _append_bounded(buffer: bytearray, chunk: bytes, limit: int) -> int:
    buffer.extend(chunk)
    dropped = max(0, len(buffer) - limit)
    if dropped:
        del buffer[:dropped]
    return dropped


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds_part}s"
    if minutes:
        return f"{minutes}m {seconds_part}s"
    return f"{seconds_part}s"


def _terminate_process_group(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


class CommandRunner:
    def __init__(self, redactor: SecretRedactor, env: Mapping[str, str] | None = None) -> None:
        self.redactor = redactor
        self.env = child_environment(env)

    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        secrets: Sequence[str] = (),
        timeout: float = COMMAND_TIMEOUT,
        check: bool = True,
        sensitive_output: bool = False,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [os.fspath(value) for value in args]
        LOG.debug("running subprocess (%d arguments)", len(command))
        try:
            result = subprocess.run(
                command,
                **({"stdin": subprocess.DEVNULL} if input_text is None else {"input": input_text}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"command timed out after {timeout:g}s: {safe_command(command, secrets)}"
            ) from None
        except OSError as exc:
            raise ToolError(
                f"could not execute {safe_command(command, secrets)}: {type(exc).__name__}"
            ) from None
        LOG.debug("command exit code: %d", result.returncode)
        if check and result.returncode != 0:
            detail = ""
            if not sensitive_output:
                raw_detail = (result.stderr or result.stdout).strip()[-2000:]
                detail = self.redactor.redact(raw_detail)
            suffix = f": {detail}" if detail else ""
            raise ToolError(
                f"command failed with exit {result.returncode}: "
                f"{safe_command(command, secrets)}{suffix}"
            )
        return result

    def run_long(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        heartbeat_label: str,
        timeout: float,
        heartbeat_interval: float = LONG_OPERATION_HEARTBEAT,
        max_output_bytes: int = MAX_LONG_OUTPUT_BYTES,
        check: bool = True,
        sensitive_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if heartbeat_interval <= 0 or max_output_bytes <= 0:
            raise ValueError("heartbeat interval and output limit must be positive")
        command = [os.fspath(value) for value in args]
        LOG.debug("running long operation: %s", safe_command(command))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolError(
                f"could not execute {safe_command(command)}: {type(exc).__name__}"
            ) from None

        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        for stream, buffer in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, buffer)

        started = time.monotonic()
        deadline = started + timeout
        next_heartbeat = started + heartbeat_interval
        timed_out = False
        try:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    timed_out = True
                    break
                wait = min(0.25, deadline - now, max(0.0, next_heartbeat - now))
                for key, _mask in selector.select(wait):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    _append_bounded(key.data, chunk, max_output_bytes)
                now = time.monotonic()
                if process.poll() is None and now >= next_heartbeat:
                    LOG.info(
                        "%s still running (elapsed %s)",
                        heartbeat_label,
                        _format_elapsed(now - started),
                    )
                    while next_heartbeat <= now:
                        next_heartbeat += heartbeat_interval
        except BaseException:
            _terminate_process_group(process)
            raise
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()

        if timed_out:
            _terminate_process_group(process)
            raise ToolError(f"{heartbeat_label} timed out after {_format_elapsed(timeout)}")
        returncode = process.wait()
        result = subprocess.CompletedProcess(
            command,
            returncode,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )
        LOG.debug("long operation exit code: %d", returncode)
        if check and returncode != 0:
            detail = ""
            if not sensitive_output:
                raw_detail = (result.stderr or result.stdout).strip()[-2000:]
                detail = self.redactor.redact(raw_detail)
            suffix = f": {detail}" if detail else ""
            raise ToolError(f"{heartbeat_label} failed with exit {returncode}{suffix}")
        return result

    def run_interactive(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
    ) -> None:
        command = [os.fspath(value) for value in args]
        LOG.debug("running interactively: %s", safe_command(command))
        try:
            with open("/dev/tty", "r+b", buffering=0) as terminal:
                result = subprocess.run(
                    command,
                    stdin=terminal,
                    stdout=terminal,
                    stderr=terminal,
                    env=self.env,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"interactive command timed out after {timeout:g}s: {safe_command(command)}"
            ) from None
        except OSError as exc:
            raise ToolError(
                f"interactive authentication requires a controlling terminal: {type(exc).__name__}"
            ) from None
        if result.returncode != 0:
            raise ToolError(
                f"interactive command failed with exit {result.returncode}: {safe_command(command)}"
            )


def check_dependencies(runner: CommandRunner) -> Dependencies:
    if sys.version_info < RECOMMENDED_PYTHON:
        LOG.warning(
            "Python %s is recommended; continuing with Python %s",
            ".".join(str(part) for part in RECOMMENDED_PYTHON),
            sys.version.split()[0],
        )
    programs = {name: shutil.which(name) for name in ("node", "npm", "systemctl", "loginctl")}
    missing = [name for name, path in programs.items() if path is None]
    if missing:
        raise ToolError(f"missing required program: {', '.join(missing)}")
    node = absolute_path(programs["node"] or "node")
    npm = absolute_path(programs["npm"] or "npm")
    systemctl = absolute_path(programs["systemctl"] or "systemctl")
    loginctl = absolute_path(programs["loginctl"] or "loginctl")
    node_result = runner.run([node, "--version"])
    npm_result = runner.run([npm, "--version"])
    node_version = node_result.stdout.strip().lstrip("v")
    npm_version = npm_result.stdout.strip()
    try:
        node_major = int(node_version.split(".", 1)[0])
    except ValueError:
        raise ToolError(f"could not parse Node.js version from {node}: {node_version!r}") from None
    if node_major < MINIMUM_NODE_MAJOR:
        raise ToolError(f"Joplin requires Node.js {MINIMUM_NODE_MAJOR}+; found {node_version}")
    if node_major < RECOMMENDED_NODE_MAJOR:
        LOG.warning(
            "Node.js %s satisfies upstream minimum but is obsolete; use an active LTS release",
            node_version,
        )
    LOG.info("Python %s", sys.version.split()[0])
    LOG.info("Node.js %s; npm %s", node_version, npm_version)
    return Dependencies(node, npm, systemctl, node_version, npm_version, loginctl)
