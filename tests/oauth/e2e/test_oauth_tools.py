"""Every advertised OAuth tool executes over TLS through the production CLI."""

from __future__ import annotations

import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.oauth.e2e.test_oauth_flow import app as app

pytestmark = pytest.mark.oauth_e2e("tools")


def exercise_all_tools(application, token):
    visited = set()

    def call(name, arguments=None):
        visited.add(name)
        return application.tool(token, name, arguments or {})

    parent = call("joplin_create_notebook", {"title": "OAuth all tools parent"})["notebook"]["id"]
    nested = call("joplin_create_notebook", {"title": "OAuth child", "parent_id": parent})[
        "notebook"
    ]["id"]
    assert call("joplin_get_notebook", {"notebook_id": nested})["notebook"]["id"] == nested
    assert (
        call("joplin_update_notebook", {"notebook_id": nested, "title": "Renamed OAuth child"})[
            "notebook"
        ]["title"]
        == "Renamed OAuth child"
    )
    assert any(item["id"] == parent for item in call("joplin_list_notebooks")["notebooks"])
    disposable = call("joplin_create_notebook", {"title": "Disposable OAuth notebook"})["notebook"][
        "id"
    ]
    call("joplin_delete_notebook", {"notebook_id": disposable})
    assert call("joplin_restore_notebook", {"notebook_id": disposable})["restored"] is True
    note = call(
        "joplin_create_note",
        {"parent_id": nested, "title": "OAuth all tools", "body": "complete OAuth tool fixture"},
    )["note"]["id"]
    assert (
        call("joplin_get_note", {"note_id": note})["note"]["body"] == "complete OAuth tool fixture"
    )
    assert (
        call("joplin_update_note", {"note_id": note, "body": "updated OAuth sentinel"})["note"][
            "body"
        ]
        == "updated OAuth sentinel"
    )
    assert any(item["id"] == note for item in call("joplin_list_notes")["notes"])
    assert any(
        item["id"] == note
        for item in call("joplin_list_notebook_notes", {"notebook_id": nested})["notes"]
    )
    deadline = time.monotonic() + 30
    while True:
        hits = call("joplin_search_notes", {"query": "id:" + note, "limit": 100})["notes"]
        if any(item["id"] == note for item in hits):
            break
        assert time.monotonic() < deadline, "Joplin search index did not catch up"
        time.sleep(0.5)
    tag = call("joplin_create_tag", {"title": "OAuth suite tag"})["tag"]["id"]
    assert call("joplin_get_tag", {"tag_id": tag})["tag"]["id"] == tag
    assert any(item["id"] == tag for item in call("joplin_list_tags")["tags"])
    assert (
        call("joplin_update_tag", {"tag_id": tag, "title": "oauth renamed tag"})["tag"]["title"]
        == "oauth renamed tag"
    )
    call("joplin_add_tag_to_note", {"tag_id": tag, "note_id": note})
    assert any(
        item["id"] == note for item in call("joplin_list_tag_notes", {"tag_id": tag})["notes"]
    )
    call("joplin_remove_tag_from_note", {"tag_id": tag, "note_id": note})
    call("joplin_delete_tag", {"tag_id": tag})
    payload = b"OAuth binary resource\x00\xff"
    resource = call(
        "joplin_create_resource",
        {
            "title": "OAuth attachment",
            "filename": "oauth.bin",
            "mime": "application/octet-stream",
            "content_base64": base64.b64encode(payload).decode(),
        },
    )["resource"]["id"]
    assert call("joplin_get_resource", {"resource_id": resource})["resource"]["id"] == resource
    assert (
        base64.b64decode(call("joplin_read_resource", {"resource_id": resource})["content_base64"])
        == payload
    )
    replacement = b"OAuth replacement\x00\xfe"
    call(
        "joplin_update_resource",
        {"resource_id": resource, "content_base64": base64.b64encode(replacement).decode()},
    )
    assert (
        base64.b64decode(call("joplin_read_resource", {"resource_id": resource})["content_base64"])
        == replacement
    )
    call("joplin_update_note", {"note_id": note, "body": f"![attachment](:/{resource})"})
    assert any(
        item["id"] == resource
        for item in call("joplin_list_note_resources", {"note_id": note})["resources"]
    )
    assert any(
        item["id"] == note
        for item in call("joplin_list_resource_notes", {"resource_id": resource})["notes"]
    )
    assert any(item["id"] == resource for item in call("joplin_list_resources")["resources"])
    call("joplin_delete_resource", {"resource_id": resource})
    call("joplin_delete_note", {"note_id": note})
    assert call("joplin_restore_note", {"note_id": note})["restored"] is True
    advertised = {tool["name"] for tool in application.rpc(token, "tools/list")["tools"]}
    assert visited == advertised, {
        "not_executed": sorted(advertised - visited),
        "not_advertised": sorted(visited - advertised),
    }
    # Clean up through the supported API; note and notebook deletion use trash.
    call("joplin_delete_note", {"note_id": note})
    for notebook in (nested, parent, disposable):
        call("joplin_delete_notebook", {"notebook_id": notebook})
    return visited


def test_every_advertised_tool_executes_and_preserves_binary_data(app):
    app.start()
    _, tokens = app.login()
    assert exercise_all_tools(app, tokens["access_token"])


def test_joplin_outage_partial_write_and_recovery_are_structured(app):
    app.start()
    _, tokens = app.login()
    access = tokens["access_token"]
    entered = []

    def down(method, path, query):
        if path == "/ping":
            entered.append(path)
            return "abort"

    app.backend.set_before_request(down)
    try:
        result = app.rpc(access, "tools/call", {"name": "joplin_list_notes", "arguments": {}})
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["retryable"] is True
        assert entered
    finally:
        app.backend.set_before_request(None)
    assert app.tool(access, "joplin_list_notes", {})["notes"] == []
    folder = app.backend.store.add_folder("Partial write fixture")
    attempts = []

    def broken_attachment(method, path, query):
        if method == "POST" and path == "/resources":
            attempts.append(path)
            return "abort"

    app.backend.set_before_request(broken_attachment)
    try:
        result = app.rpc(
            access,
            "tools/call",
            {
                "name": "joplin_create_note",
                "arguments": {
                    "parent_id": folder,
                    "title": "Partially applied fixture",
                    "attachments": [
                        {
                            "filename": "fixture.bin",
                            "mime": "application/octet-stream",
                            "content_base64": base64.b64encode(b"fixture").decode(),
                        }
                    ],
                },
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"].startswith("PARTIAL_")
        assert len(app.backend.store.notes) == 1
        assert attempts == ["/resources"]
    finally:
        app.backend.set_before_request(None)


def test_tool_execution_is_bounded_without_dropping_waiting_calls(app):
    app.start()
    _, tokens = app.login()
    guard, release = threading.Lock(), threading.Event()
    running = []

    def hold(method, path, query):
        with guard:
            running.append(path)
        try:
            release.wait(10)
        finally:
            with guard:
                running.remove(path)

    app.backend.set_before_request(hold)
    try:
        with ThreadPoolExecutor(max_workers=9) as pool:
            calls = [
                pool.submit(
                    app.rpc,
                    tokens["access_token"],
                    "tools/call",
                    {"name": "joplin_list_notes", "arguments": {}},
                )
                for _ in range(9)
            ]
            deadline = time.monotonic() + 5
            while len(running) < 8:
                assert time.monotonic() < deadline, "Workers did not start the admitted calls"
                time.sleep(0.02)
            # The ninth admitted call waits for one of the eight fixed workers.
            time.sleep(0.3)
            assert len(running) == 8
            release.set()
            results = [call.result(timeout=10) for call in calls]
    finally:
        release.set()
        app.backend.set_before_request(None)
    assert all(result["isError"] is False for result in results)
