"""Grant recovery after identity outages that follow a completed GitHub rotation."""

from __future__ import annotations

import pytest

from tests.oauth.e2e.test_oauth_flow import app as app
from tests.oauth.e2e.test_oauth_lifecycle import refresh
from tests.oauth.e2e.test_oauth_security import resource

pytestmark = pytest.mark.oauth_e2e("recovery")

OUTAGES = [
    pytest.param(503, {}, id="unavailable"),
    pytest.param(429, {}, id="rate-limited"),
    pytest.param(403, {"X-RateLimit-Remaining": "0"}, id="secondary-rate-limit"),
]


def create_note(app, token, title):
    return app.rpc(
        token,
        "tools/call",
        {
            "name": "joplin_create_note",
            "arguments": {"parent_id": app.parent, "title": title, "body": "fixture body"},
        },
    )


@pytest.mark.parametrize("status,headers", OUTAGES)
def test_explicit_refresh_survives_identity_outage_after_rotation(app, status, headers):
    app.start()
    client_id, tokens = app.login()
    github = app.github.github
    github.user_failures_after_refresh = (status, headers, 1)
    failed = refresh(app, client_id, tokens["refresh_token"])
    assert failed.status_code == 503
    assert failed.headers["retry-after"]
    assert github.refresh_count == 1
    # The rotated upstream pair was persisted; the downstream grant is intact.
    renewed = refresh(app, client_id, tokens["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    assert github.revoked_refresh_presented == 0
    assert resource(app, renewed.json()["access_token"]).status_code == 200
    assert refresh(app, client_id, tokens["refresh_token"]).status_code == 401
    legacy = app.rpc(app.mcp_token, "tools/list", path="/mcp")
    assert legacy["tools"]


def test_explicit_refresh_recovery_survives_restart(app):
    app.start()
    client_id, tokens = app.login()
    github = app.github.github
    github.user_failures_after_refresh = (503, {}, 1)
    assert refresh(app, client_id, tokens["refresh_token"]).status_code == 503
    app.stop()
    app.start()
    renewed = refresh(app, client_id, tokens["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    assert github.revoked_refresh_presented == 0
    assert resource(app, renewed.json()["access_token"]).status_code == 200


def test_transparent_refresh_survives_identity_outage_without_running_tools(app):
    app.github.github.access_ttl = 120
    app.start()
    _, tokens = app.login()
    github = app.github.github
    app.parent = app.backend.store.add_folder("Recovery")
    app.advance(121)
    # The rotated token's first check and FastMCP's re-read check both fail.
    github.user_failures_after_refresh = (503, {}, 2)
    blocked = app.client.post(
        "/claude/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "joplin_create_note",
                "arguments": {"parent_id": app.parent, "title": "blocked", "body": "x"},
            },
        },
        headers={
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        },
    )
    assert blocked.status_code == 503
    assert github.refresh_count == 1
    assert not [note for note in app.backend.store.notes.values() if note["title"] == "blocked"]
    created = create_note(app, tokens["access_token"], "recovered")
    assert created["isError"] is False, created
    assert github.refresh_count == 1
    assert github.revoked_refresh_presented == 0
