from __future__ import annotations

import json

import pytest

from joplin_md_sync.mcp_service import JoplinMcpService, ToolServiceError


@pytest.mark.parametrize(
    "value",
    [
        None,
        3,
        "fas fa-book",
        "null",
        "[]",
        "{}",
        '{"type":true,"name":"fas fa-book"}',
        '{"type":0,"emoji":"x"}',
        '{"type":4,"name":"x"}',
        '{"type":1}',
        '{"type":2}',
        '{"type":3}',
        '{"type":3,"name":7}',
        '{"type":3,"name":"fas fa-book","extra":true}',
    ],
)
def test_folder_icon_rejects_values_that_joplin_cannot_unserialize(value: object) -> None:
    with pytest.raises(ToolServiceError) as caught:
        JoplinMcpService._folder_icon(value)
    assert caught.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    ("value", "expected_type", "expected_field", "expected_value"),
    [
        (r'{"type":1,"emoji":"\ud83d\udcda"}', 1, "emoji", "\U0001f4da"),
        ('{"type":2,"dataUrl":"data:image/png;base64,AA=="}', 2, "dataUrl", "data:image/png;base64,AA=="),
        ('{"type":3,"name":"fas fa-book"}', 3, "name", "fas fa-book"),
    ],
)
def test_folder_icon_normalizes_supported_joplin_icon_objects(
    value: str, expected_type: int, expected_field: str, expected_value: str
) -> None:
    parsed = json.loads(JoplinMcpService._folder_icon(value))
    assert parsed == {
        "type": expected_type,
        "emoji": "",
        "name": "",
        "dataUrl": "",
        expected_field: expected_value,
    }


def test_folder_icon_empty_string_clears_icon() -> None:
    assert JoplinMcpService._folder_icon("") == ""
