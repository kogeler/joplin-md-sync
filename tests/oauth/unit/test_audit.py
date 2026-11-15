"""OAuth audit events: validated fields, bounded volume, default visibility."""

from __future__ import annotations

import argparse
import logging
from types import SimpleNamespace

import pytest

from joplin_md_sync import cli, oauth_audit


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(value=0.0)
    monkeypatch.setattr(oauth_audit, "_throttle", oauth_audit._Throttle())
    monkeypatch.setattr(oauth_audit, "time", SimpleNamespace(monotonic=lambda: now.value))
    return now


def lines(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def test_only_whitelisted_and_valid_values_are_written(clock, caplog) -> None:
    caplog.set_level(logging.INFO, logger=oauth_audit.AUDIT_LOGGER)
    oauth_audit.audit(
        "client_registered",
        client="0123456789abcdef",
        pending=3,
        reason="x y\nevent=forged",
        client_name="secret-name",
    )
    assert lines(caplog) == [
        "oauth_audit event=client_registered client=01234567 reason=invalid pending=3"
    ]


def test_anonymous_events_are_throttled_with_suppressed_counts(clock, caplog) -> None:
    caplog.set_level(logging.INFO, logger=oauth_audit.AUDIT_LOGGER)
    for _ in range(3):
        oauth_audit.audit("rate_limited", bucket="register")
    oauth_audit.audit("rate_limited", bucket="browser")
    clock.value = oauth_audit.WINDOW_SECONDS
    oauth_audit.audit("rate_limited", bucket="register")
    assert lines(caplog) == [
        "oauth_audit event=rate_limited bucket=register",
        "oauth_audit event=rate_limited bucket=browser",
        "oauth_audit event=rate_limited bucket=register suppressed=2",
    ]


def test_owner_events_are_capped_per_window(clock, caplog) -> None:
    caplog.set_level(logging.INFO, logger=oauth_audit.AUDIT_LOGGER)
    for _ in range(oauth_audit.OWNER_LINES_PER_WINDOW + 5):
        oauth_audit.audit("refresh_succeeded", client="owner")
    assert len(lines(caplog)) == oauth_audit.OWNER_LINES_PER_WINDOW


@pytest.mark.parametrize(
    ("quiet", "shown"),
    [(False, ["INFO audit", "WARNING audit", "WARNING other"]), (True, ["WARNING audit"])],
)
def test_cli_logging_shows_security_events_without_verbose(capsys, quiet, shown) -> None:
    cli._setup_logging(argparse.Namespace(quiet=quiet, verbose=False))
    try:
        audit = logging.getLogger(oauth_audit.AUDIT_LOGGER)
        other = logging.getLogger("joplin_md_sync.other")
        audit.info("INFO audit")
        audit.warning("WARNING audit")
        other.info("INFO other")
        other.warning("WARNING other")
    finally:
        cli._close_logging_handlers()
    output = capsys.readouterr().err
    assert [line for line in shown if line in output] == shown
    hidden = {"INFO audit", "WARNING audit", "INFO other", "WARNING other"} - set(shown)
    assert not [line for line in hidden if line in output]
