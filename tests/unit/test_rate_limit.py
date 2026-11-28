"""Request rate budgets shared by the public OAuth endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import rate_limit
from joplin_md_sync.rate_limit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


@pytest.mark.parametrize("per_minute", (0, -1))
def test_rate_limit_must_be_positive(per_minute: int) -> None:
    with pytest.raises(ValueError, match="rate limit must be positive"):
        TokenBucket(per_minute)


def test_bucket_allows_a_burst_then_reports_retry_after_and_refills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    bucket = TokenBucket(6)  # one token every ten seconds

    assert [bucket.consume() for _ in range(6)] == [(True, 0)] * 6
    assert bucket.consume() == (False, 10)

    clock.now += 4
    assert bucket.consume() == (False, 6)
    clock.now += 6
    assert bucket.consume() == (True, 0)
    assert bucket.consume() == (False, 10)

    # Idle time never stores more than one burst.
    clock.now += 3_600
    assert [bucket.consume() for _ in range(6)] == [(True, 0)] * 6
    assert bucket.consume()[0] is False


def test_retry_after_is_at_least_one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    bucket = TokenBucket(600)  # one token every 0.1 seconds
    for _ in range(600):
        assert bucket.consume() == (True, 0)
    assert bucket.consume() == (False, 1)
