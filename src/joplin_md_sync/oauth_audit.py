"""Credential-free OAuth security events with bounded log volume.

Library logs stay silenced because they can contain codes and tokens. These
events are the operator's trail instead: fixed names, whitelisted fields and
validated values only, never request text, credentials or client metadata.
"""

from __future__ import annotations

import logging
import re
import threading
import time

AUDIT_LOGGER = "joplin_md_sync.oauth.audit"
WINDOW_SECONDS = 60.0
OWNER_LINES_PER_WINDOW = 30

log = logging.getLogger(AUDIT_LOGGER)

# Anything an anonymous client can trigger writes one line per window and key;
# the next written line reports how many were suppressed meanwhile.
EVENTS: dict[str, tuple[int, int]] = {
    "service_started": (logging.INFO, OWNER_LINES_PER_WINDOW),
    "client_registered": (logging.INFO, 1),
    "registration_rejected": (logging.WARNING, 1),
    "consent_approved": (logging.INFO, OWNER_LINES_PER_WINDOW),
    "consent_denied": (logging.INFO, OWNER_LINES_PER_WINDOW),
    "consent_rejected": (logging.WARNING, 1),
    "consent_warning_missing": (logging.ERROR, 1),
    "callback_rejected": (logging.WARNING, 1),
    "foreign_account": (logging.WARNING, 1),
    "login_denied": (logging.WARNING, 1),
    "token_issued": (logging.INFO, OWNER_LINES_PER_WINDOW),
    "refresh_succeeded": (logging.INFO, OWNER_LINES_PER_WINDOW),
    "refresh_denied": (logging.WARNING, 1),
    "resource_denied": (logging.WARNING, 1),
    "rate_limited": (logging.WARNING, 1),
    "github_unavailable": (logging.WARNING, 1),
    "state_reset": (logging.WARNING, OWNER_LINES_PER_WINDOW),
}
FIELDS = (
    "client",
    "reason",
    "bucket",
    "stage",
    "github_id",
    "new_client",
    "established",
    "pending",
)
# Separate windows per reason, bucket, stage and account.
KEY_FIELDS = ("reason", "bucket", "stage", "github_id")
CLIENT_PREFIX = 8
VALUE = re.compile(r"[A-Za-z0-9_-]{1,32}")
MAX_WINDOWS = 256


class _Throttle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, ...], tuple[float, int, int]] = {}

    def admit(self, key: tuple[str, ...], limit: int, now: float) -> int | None:
        """Return the suppressed count to report if a line may be written."""
        with self._lock:
            start, written, suppressed = self._windows.get(key, (now, 0, 0))
            if now - start >= WINDOW_SECONDS:
                start, written = now, 0
            if written < limit:
                self._windows[key] = (start, written + 1, 0)
                return suppressed
            self._windows[key] = (start, written, suppressed + 1)
            if len(self._windows) > MAX_WINDOWS:
                self._windows = {
                    item: window
                    for item, window in self._windows.items()
                    if now - window[0] < WINDOW_SECONDS or window[2]
                }
            return None


_throttle = _Throttle()


def audit(event: str, **fields: object) -> None:
    level, limit = EVENTS[event]
    values: dict[str, str] = {}
    for name in FIELDS:
        if name not in fields:
            continue
        text = str(fields[name])
        if name == "client":
            text = text[:CLIENT_PREFIX]
        values[name] = text if VALUE.fullmatch(text) else "invalid"
    key = (event, *(values.get(name, "") for name in KEY_FIELDS))
    suppressed = _throttle.admit(key, limit, time.monotonic())
    if suppressed is None:
        return
    if suppressed:
        values["suppressed"] = str(suppressed)
    log.log(
        level,
        "oauth_audit event=%s%s",
        event,
        "".join(f" {name}={value}" for name, value in values.items()),
    )
