#!/usr/bin/env python3
"""Keep one full Joplin Terminal TUI, recurrent sync, and Data API alive."""

from __future__ import annotations

import argparse
import errno
import fcntl
import logging
import os
import pty
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import time

from joplin_terminal_common import (
    DEFAULT_API_PORT,
    DEFAULT_SYNC_INTERVAL,
    ProfileLock,
    ToolError,
    absolute_path,
    child_environment,
    ping_api,
    resolved_path,
)

LOG = logging.getLogger("joplin-terminal-supervisor")
_ALT_SCREEN = b"\x1b[?1049h"
_CURSOR_QUERY = b"\x1b[6n"
_CURSOR_RESPONSE = b"\x1b[24;1R"
_MAX_TUI_BUFFER = 128 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one Joplin Terminal TUI with sync, decryption, and local Data API."
    )
    parser.add_argument("--node-path", required=True, metavar="PATH")
    parser.add_argument("--joplin-path", required=True, metavar="PATH")
    parser.add_argument("--profile-dir", required=True, metavar="PATH")
    parser.add_argument("--lock-file", required=True, metavar="PATH")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--sync-interval", type=int, default=DEFAULT_SYNC_INTERVAL)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--shutdown-timeout", type=float, default=20.0)
    parser.add_argument("--health-interval", type=float, default=30.0)
    parser.add_argument("--health-failures", type=int, default=3)
    parser.add_argument("--verbose", action="store_true")
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def port_has_listener(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.node_path = absolute_path(args.node_path)
        self.joplin_path = absolute_path(args.joplin_path)
        self.profile_dir = resolved_path(args.profile_dir)
        self.lock_file = resolved_path(args.lock_file)
        self.api_port = args.api_port
        self.sync_interval = args.sync_interval
        self.startup_timeout = args.startup_timeout
        self.shutdown_timeout = args.shutdown_timeout
        self.health_interval = args.health_interval
        self.health_failures = args.health_failures
        self._requested_signal: int | None = None
        self._child_pid: int | None = None
        self._pty_fd: int | None = None

    def validate(self) -> None:
        if not self.node_path.is_file() or not os.access(self.node_path, os.X_OK):
            raise ToolError(f"Node.js executable is not runnable: {self.node_path}")
        if not self.joplin_path.is_file() or not os.access(self.joplin_path, os.X_OK):
            raise ToolError(f"Joplin executable is not runnable: {self.joplin_path}")
        if not self.profile_dir.is_dir():
            raise ToolError(f"Joplin profile does not exist: {self.profile_dir}")
        if not 1 <= self.api_port <= 65535:
            raise ToolError("--api-port must be between 1 and 65535")
        if self.startup_timeout <= 0 or self.shutdown_timeout <= 0:
            raise ToolError("startup and shutdown timeouts must be positive")
        if self.health_interval <= 0 or self.health_failures < 1:
            raise ToolError("health interval must be positive and failures must be at least 1")
        self._check_node_runtime()

    def _check_node_runtime(self) -> None:
        try:
            result = subprocess.run(
                [self.node_path, "--version"],
                env=child_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Node.js runtime self-check timed out: {self.node_path}") from None
        except OSError as exc:
            raise ToolError(
                f"Node.js runtime self-check failed for {self.node_path}: {exc.strerror}"
            ) from None
        if result.returncode == 0:
            return
        detail = " ".join(f"{result.stdout}\n{result.stderr}".split())[:300]
        suffix = f": {detail}" if detail else ""
        raise ToolError(
            f"Node.js runtime self-check failed for {self.node_path} "
            f"(exit {result.returncode}){suffix}"
        )

    def _check_profile_writable(self) -> None:
        probe = self.profile_dir / f".joplin-terminal-service-write-test-{os.getpid()}"
        fd: int | None = None
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, b"ok\n")
        except OSError as exc:
            raise ToolError(
                f"Joplin profile is not writable inside the service sandbox: {exc.strerror}"
            ) from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def request_shutdown(self, signum: int, _frame: object) -> None:
        if self._requested_signal is None:
            self._requested_signal = signum

    def _spawn(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            env = child_environment()
            env.update({"TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24"})
            argv = [
                str(self.node_path),
                str(self.joplin_path),
                "--profile",
                str(self.profile_dir),
            ]
            os.execve(str(self.node_path), argv, env)
        self._child_pid = pid
        self._pty_fd = fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.set_blocking(fd, False)

    def _read_pty(self) -> bytes:
        assert self._pty_fd is not None
        try:
            return os.read(self._pty_fd, 65536)
        except BlockingIOError:
            return b""
        except OSError as exc:
            if exc.errno == errno.EIO:
                return b""
            raise

    def _write_pty(self, data: bytes) -> None:
        assert self._pty_fd is not None
        view = memoryview(data)
        while view:
            try:
                written = os.write(self._pty_fd, view)
                view = view[written:]
            except BlockingIOError:
                time.sleep(0.01)

    def _child_status(self) -> int | None:
        assert self._child_pid is not None
        waited, status = os.waitpid(self._child_pid, os.WNOHANG)
        if not waited:
            return None
        self._child_pid = None
        return os.waitstatus_to_exitcode(status)

    def _start_api(self) -> None:
        assert self._pty_fd is not None
        selector = selectors.DefaultSelector()
        selector.register(self._pty_fd, selectors.EVENT_READ)
        deadline = time.monotonic() + self.startup_timeout
        terminal_buffer = bytearray()
        alt_screen_seen = False
        last_output = time.monotonic()
        colon_sent_at: float | None = None
        cursor_query_at: float | None = None
        cursor_answered_at: float | None = None
        command_sent = False
        attempts = 0
        last_ping_reason = "not attempted"

        while time.monotonic() < deadline:
            if self._requested_signal is not None:
                return
            child_code = self._child_status()
            if child_code is not None:
                raise ToolError(f"Joplin exited before API readiness (exit {child_code})")

            for _key, _mask in selector.select(0.05):
                chunk = self._read_pty()
                if chunk:
                    terminal_buffer.extend(chunk)
                    if len(terminal_buffer) > _MAX_TUI_BUFFER:
                        del terminal_buffer[:-_MAX_TUI_BUFFER]
                    last_output = time.monotonic()
                    alt_screen_seen = alt_screen_seen or _ALT_SCREEN in terminal_buffer

            now = time.monotonic()
            if alt_screen_seen and colon_sent_at is None and now - last_output >= 0.30:
                self._write_pty(b":")
                colon_sent_at = now
                attempts += 1
                LOG.debug("entered Joplin command mode (attempt %d)", attempts)

            if colon_sent_at is not None and cursor_query_at is None:
                query_index = terminal_buffer.rfind(_CURSOR_QUERY)
                if query_index >= 0:
                    cursor_query_at = now

            # terminal-kit attaches its response handler just after writing the
            # query. A short protocol delay avoids racing that handler.
            if (
                cursor_query_at is not None
                and cursor_answered_at is None
                and now - cursor_query_at >= 0.15
            ):
                self._write_pty(_CURSOR_RESPONSE)
                cursor_answered_at = now

            if (
                cursor_answered_at is not None
                and not command_sent
                and now - cursor_answered_at >= 0.20
            ):
                self._write_pty(b"server start --exit-early\r")
                command_sent = True
                LOG.debug("requested Joplin Data API startup")

            if colon_sent_at is not None and cursor_query_at is None and now - colon_sent_at > 2:
                if attempts >= 3:
                    raise ToolError("Joplin TUI did not enter command mode")
                colon_sent_at = None
                terminal_buffer.clear()

            if command_sent:
                result = ping_api(self.api_port, timeout=0.5)
                last_ping_reason = result.reason
                if result.ok:
                    LOG.info("Joplin Data API is ready on 127.0.0.1:%d", self.api_port)
                    return

        raise ToolError(
            f"Joplin Data API did not become ready within {self.startup_timeout:g}s "
            f"({last_ping_reason})"
        )

    def _signal_child(self, signum: int) -> None:
        if self._child_pid is None:
            return
        try:
            os.killpg(self._child_pid, signum)
        except ProcessLookupError:
            return

    def _stop_child(self, signum: int = signal.SIGTERM) -> int:
        if self._child_pid is None:
            return 0
        self._signal_child(signum)
        deadline = time.monotonic() + self.shutdown_timeout
        while time.monotonic() < deadline:
            code = self._child_status()
            if code is not None:
                return code
            if self._pty_fd is not None:
                self._read_pty()
            time.sleep(0.05)
        LOG.warning("Joplin did not stop within %.1fs; sending SIGKILL", self.shutdown_timeout)
        self._signal_child(signal.SIGKILL)
        assert self._child_pid is not None
        _waited, status = os.waitpid(self._child_pid, 0)
        self._child_pid = None
        return os.waitstatus_to_exitcode(status)

    def _monitor(self) -> int:
        assert self._pty_fd is not None
        selector = selectors.DefaultSelector()
        selector.register(self._pty_fd, selectors.EVENT_READ)
        next_health = time.monotonic() + self.health_interval
        failures = 0
        while True:
            if self._requested_signal is not None:
                signum = self._requested_signal
                LOG.info("received %s; stopping Joplin", signal.Signals(signum).name)
                self._stop_child(signum)
                return 0
            child_code = self._child_status()
            if child_code is not None:
                LOG.error("Joplin exited unexpectedly (exit %d)", child_code)
                return 1
            for _key, _mask in selector.select(0.25):
                self._read_pty()  # Deliberately discard note-bearing TUI output.
            now = time.monotonic()
            if now < next_health:
                continue
            result = ping_api(self.api_port, timeout=0.75)
            if result.ok:
                failures = 0
            else:
                failures += 1
                LOG.warning(
                    "Joplin Data API healthcheck failed (%s, %d/%d)",
                    result.reason,
                    failures,
                    self.health_failures,
                )
                if failures >= self.health_failures:
                    self._stop_child()
                    return 1
            next_health = now + self.health_interval

    def run(self) -> int:
        self.validate()
        if port_has_listener(self.api_port):
            raise ToolError(f"API port 127.0.0.1:{self.api_port} is already in use")
        with ProfileLock(self.lock_file):
            LOG.info("starting Joplin with profile %s", self.profile_dir)
            LOG.info("configured recurrent sync interval: %ds", self.sync_interval)
            try:
                self._check_profile_writable()
                self._spawn()
                self._start_api()
                return self._monitor()
            except BaseException:
                if self._child_pid is not None:
                    self._stop_child()
                raise
            finally:
                if self._pty_fd is not None:
                    os.close(self._pty_fd)
                    self._pty_fd = None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    supervisor = Supervisor(args)
    signal.signal(signal.SIGTERM, supervisor.request_shutdown)
    signal.signal(signal.SIGINT, supervisor.request_shutdown)
    try:
        return supervisor.run()
    except ToolError as exc:
        LOG.error("%s", exc)
        return 1
    except Exception as exc:
        if args.verbose:
            LOG.exception("unexpected supervisor failure")
        else:
            LOG.error("unexpected supervisor failure: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
