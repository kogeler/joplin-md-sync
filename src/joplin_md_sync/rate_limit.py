"""Thread-safe request rate budgets for public HTTP endpoints."""

from __future__ import annotations

import math
import threading
import time


class TokenBucket:
    """Thread-safe bounded token bucket; `consume` returns (allowed, Retry-After seconds)."""

    def __init__(self, per_minute: int) -> None:
        if per_minute <= 0:
            raise ValueError("rate limit must be positive")
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)
        self._rate = float(per_minute) / 60.0
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._updated) * self._rate,
            )
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True, 0
            return False, max(1, math.ceil((1 - self._tokens) / self._rate))
