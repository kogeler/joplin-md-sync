"""Tests for the interactive public ChatGPT Actions setup assistant."""

from __future__ import annotations

import json
import ssl
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import prepare_chatgpt_action as assistant  # noqa: E402

TOKEN = "A" * 43


def test_tls_context_requires_valid_public_certificate_and_tls_1_2() -> None:
    context = assistant.create_tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_normalize_origin_accepts_only_a_bare_host_or_https_origin() -> None:
    assert assistant.normalize_origin("notes.example.com") == "https://notes.example.com"
    assert assistant.normalize_origin("https://notes.example.com/") == "https://notes.example.com"
    for invalid in (
        "",
        "http://notes.example.com",
        "https://user@notes.example.com",
        "https://notes.example.com:8443",
        "https://notes.example.com/path",
    ):
        with pytest.raises(assistant.SetupError):
            assistant.normalize_origin(invalid)


def test_run_setup_checks_public_actions_endpoint_and_writes_token_free_contract(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def request(
        origin: str,
        path: str,
        method: str,
        token: str | None,
        body: bytes | None,
    ) -> assistant.HttpResponse:
        del body
        calls.append((path, method, token))
        if token is None:
            return assistant.HttpResponse(401, b'{"success":false}')
        assert origin == "https://notes.example.com"
        return assistant.HttpResponse(
            200,
            b'{"success":true,"result":{},"request_id":"request-1"}',
        )

    output = tmp_path / "chatgpt-action.openapi.json"
    messages: list[str] = []
    origin, operation_count = assistant.run_setup(
        "notes.example.com",
        TOKEN,
        output=output,
        requester=request,
        report=messages.append,
    )

    assert origin == "https://notes.example.com"
    assert operation_count > 0
    assert calls == [
        (assistant.ACTION_PATH, "POST", None),
        (assistant.ACTION_PATH, "POST", TOKEN),
    ]
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["servers"] == [{"url": origin}]
    assert assistant.ACTION_PATH in document["paths"]
    assert TOKEN not in output.read_text(encoding="utf-8")
    assert messages[-1] == "[3/3] Generating the OpenAPI contract..."


def test_run_setup_does_not_write_contract_when_endpoint_check_fails(
    tmp_path: Path,
) -> None:
    def request(
        origin: str,
        path: str,
        method: str,
        token: str | None,
        body: bytes | None,
    ) -> assistant.HttpResponse:
        del origin, path, method, token, body
        return assistant.HttpResponse(302, b"")

    output = tmp_path / "chatgpt-action.openapi.json"
    with pytest.raises(assistant.SetupError, match="unauthenticated Actions request"):
        assistant.run_setup(
            "notes.example.com",
            TOKEN,
            output=output,
            requester=request,
            report=lambda message: None,
        )
    assert not output.exists()


def test_run_setup_rejects_invalid_token_before_network_access(tmp_path: Path) -> None:
    called = False

    def request(
        origin: str,
        path: str,
        method: str,
        token: str | None,
        body: bytes | None,
    ) -> assistant.HttpResponse:
        nonlocal called
        del origin, path, method, token, body
        called = True
        return assistant.HttpResponse(200, b"")

    with pytest.raises(assistant.SetupError, match="at least 32 bytes"):
        assistant.run_setup(
            "notes.example.com",
            "A" * 20,
            output=tmp_path / "chatgpt-action.openapi.json",
            requester=request,
            report=lambda message: None,
        )
    assert not called


def test_main_prompts_only_for_host_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def read_host(prompt: str) -> str:
        prompts.append(prompt)
        return "notes.example.com"

    def read_token(prompt: str) -> str:
        prompts.append(prompt)
        return TOKEN

    def setup(host: str, token: str) -> tuple[str, int]:
        assert host == "https://notes.example.com"
        assert token == TOKEN
        return host, 27

    monkeypatch.setattr("builtins.input", read_host)
    monkeypatch.setattr(assistant.getpass, "getpass", read_token)
    monkeypatch.setattr(assistant, "run_setup", setup)

    assert assistant.main() == 0
    assert prompts == [
        "Public Actions host (for example notes.example.com): ",
        "GPT Actions bearer token (input hidden): ",
    ]
