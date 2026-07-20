"""Deterministic GPT Actions OpenAPI contract generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.gpt_openapi import (
    CANONICAL_SERVER_URL,
    generate_openapi,
    registry_for_export,
    render_openapi,
    validate_server_url,
)
from joplin_md_sync.tool_registry import action_route, operation_id, tool_effect
from tests.helpers import run_cli


def test_generated_operations_match_exposed_registry() -> None:
    registry = registry_for_export()
    document = generate_openapi(registry, CANONICAL_SERVER_URL)
    operations = {
        path: item["post"] for path, item in document["paths"].items()
    }
    assert len(operations) == len(registry.exposed) == 27
    for tool in registry.exposed:
        path = f"/api/gpt/v1/tools/{action_route(tool)}"
        operation = operations[path]
        assert operation["operationId"] == operation_id(tool)
        assert operation["security"] == [{"GPTActionBearer": []}]
        assert operation["x-openai-isConsequential"] is (tool_effect(tool) != "read")
        assert operation["requestBody"]["required"] is True
        assert operation["requestBody"]["content"]["application/json"]["schema"] == (
            tool.to_mcp_json()["inputSchema"]
        )
    paths = "\n".join(operations)
    for disabled in (
        "joplin_read_resource",
        "joplin_create_resource",
        "joplin_update_resource",
    ):
        assert disabled not in paths


def test_contract_is_deterministic_and_secret_free() -> None:
    registry = registry_for_export()
    first = render_openapi(registry, CANONICAL_SERVER_URL)
    assert first == render_openapi(registry, CANONICAL_SERVER_URL)
    parsed = json.loads(first)
    assert parsed["openapi"] == "3.1.0"
    assert parsed["servers"] == [{"url": CANONICAL_SERVER_URL}]
    assert "token" not in json.dumps(parsed).casefold()


@pytest.mark.parametrize(
    "url",
    (
        "http://notes.example.com",
        "https://notes.example.com:8443",
        "https://user:secret@notes.example.com",
        "https://notes.example.com/base",
        "https://notes.example.com?secret=x",
    ),
)
def test_production_server_url_is_strict(url: str) -> None:
    with pytest.raises(ValueError):
        validate_server_url(url)


def test_cli_exports_contract_and_advertises_optional_transport(tmp_path: Path) -> None:
    output = tmp_path / "action.json"
    result = run_cli(
        "gpt-actions",
        "export-openapi",
        "--server-url",
        "https://notes.example.com",
        "--output",
        str(output),
        "--json",
    )
    assert result.exit_code == 0
    assert result.json["operation_count"] == len(registry_for_export().exposed)
    assert json.loads(output.read_text(encoding="utf-8"))["servers"] == [
        {"url": "https://notes.example.com"}
    ]
    capabilities = run_cli("capabilities", "--json").json
    assert "gpt-actions export-openapi" in capabilities["commands"]
    assert capabilities["features"]["gpt_actions_optional_transport"] is True
