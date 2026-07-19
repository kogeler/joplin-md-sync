#!/usr/bin/env python3
"""Small fake for PTY/E2EE and supervisor process tests."""

from __future__ import annotations

import getpass
import http.server
import os
import signal
import sys
import termios
import threading
import time
import tty
from pathlib import Path


class PingHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ping":
            body = b"JoplinClipperServer"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        pass


def run_e2ee(mode: str) -> int:
    if mode == "e2ee_timeout":
        time.sleep(60)
        return 0
    if mode == "e2ee_no_key":
        print("Fatal error: masterKeyNotLoaded", flush=True)
        return 1
    if mode == "e2ee_crash":
        return 9
    if mode == "e2ee_large_output":
        print("private notebook title\n" * 20000, end="", flush=True)
    prompt = (
        "Master password required: " if mode == "e2ee_changed_prompt" else "Enter master password: "
    )
    password = getpass.getpass(prompt)
    if mode == "e2ee_wrong" or password != "correct-password":
        print("Invalid password", flush=True)
        return 0
    print("Starting decryption...", flush=True)
    if mode == "e2ee_large_output":
        time.sleep(0.08)
    print("Decrypted items: 2", flush=True)
    print("Completed decryption.", flush=True)
    return 0


def start_api(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), PingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_tui(mode: str) -> int:
    pid_file = os.environ.get("FAKE_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    if mode == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    if mode == "crash":
        os.write(sys.stdout.fileno(), b"\x1b[?1049h\x1b[24;1H")
        return 7

    original = termios.tcgetattr(sys.stdin.fileno())
    tty.setraw(sys.stdin.fileno())
    server: http.server.ThreadingHTTPServer | None = None
    try:
        os.write(
            sys.stdout.fileno(),
            b"\x1b[?1049h\x1b[2Jfake private note body\x1b[24;1Hready",
        )
        buffer = bytearray()
        command_mode = False
        while True:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                return 0
            buffer.extend(chunk)
            if not command_mode and b":" in buffer:
                command_mode = True
                buffer.clear()
                os.write(sys.stdout.fileno(), b":\x1b[?25h\x1b[6n")
                continue
            if command_mode and b"server start --exit-early\r" in buffer:
                if mode != "no_api":
                    server = start_api(int(os.environ["FAKE_API_PORT"]))
                    if mode == "health_loss":
                        delay = float(os.environ.get("FAKE_HEALTH_LOSS_DELAY", "0.5"))

                        def stop_later(
                            target: http.server.ThreadingHTTPServer = server,
                            wait: float = delay,
                        ) -> None:
                            time.sleep(wait)
                            target.shutdown()
                            target.server_close()

                        threading.Thread(target=stop_later, daemon=True).start()
                command_mode = False
                buffer.clear()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)


def main() -> int:
    mode = os.environ.get("FAKE_JOPLIN_MODE", "success")
    if "e2ee" in sys.argv:
        return run_e2ee(mode)
    return run_tui(mode)


if __name__ == "__main__":
    raise SystemExit(main())
