#!/usr/bin/env python3
"""Verify a public GPT Actions endpoint and create its OpenAPI contract."""

from __future__ import annotations

import getpass
import json
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from joplin_md_sync.auth import validate_bearer_token  # noqa: E402
from joplin_md_sync.gpt_openapi import (  # noqa: E402
    ACTION_PATH_PREFIX,
    generate_openapi,
    registry_for_export,
    validate_server_url,
)
from joplin_md_sync.workspace import write_file_atomic  # noqa: E402

OUTPUT = REPO / "chatgpt-action.openapi.json"
ACTION_NAME = "joplin_list_notebooks"
ACTION_PATH = f"{ACTION_PATH_PREFIX}/{ACTION_NAME}"
REQUEST_BODY = b'{"limit":1}'
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 1_000_000


class SetupError(RuntimeError):
    """A public endpoint or generated contract failed validation."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


Requester = Callable[[str, str, str, str | None, bytes | None], HttpResponse]
Reporter = Callable[[str], None]


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep a bearer credential on the exact origin entered by the operator."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def normalize_origin(host: str) -> str:
    """Accept a hostname or HTTPS origin and return a validated origin."""

    value = host.strip()
    if not value:
        raise SetupError("public Actions host is required")
    candidate = value if "://" in value else f"https://{value}"
    try:
        return validate_server_url(candidate)
    except ValueError as exc:
        raise SetupError(str(exc)) from None


def _read_limited(stream: BinaryIO) -> bytes:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise SetupError("endpoint response exceeds the 1 MB setup limit")
    return body


def create_tls_context() -> ssl.SSLContext:
    """Create a public-Web TLS context with strict certificate validation."""

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def https_request(
    origin: str,
    path: str,
    method: str,
    token: str | None,
    body: bytes | None,
) -> HttpResponse:
    """Make one bounded HTTPS request without following redirects."""

    headers = {
        "Accept": "application/json",
        "User-Agent": "joplin-md-sync-chatgpt-setup/1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{origin}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=create_tls_context()),
        _RejectRedirects(),
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return HttpResponse(response.status, _read_limited(response))
    except urllib.error.HTTPError as exc:
        with exc:
            return HttpResponse(exc.code, _read_limited(exc))
    except (OSError, urllib.error.URLError) as exc:
        raise SetupError(f"HTTPS request to {origin}{path} failed: {exc}") from None


def _expect_status(response: HttpResponse, expected: set[int], check: str) -> None:
    if response.status not in expected:
        wanted = "/".join(str(status) for status in sorted(expected))
        raise SetupError(f"{check}: expected HTTP {wanted}, received {response.status}")


def verify_public_endpoint(
    origin: str,
    token: str,
    *,
    requester: Requester = https_request,
    report: Reporter = print,
) -> None:
    """Verify Actions authentication and one real read operation."""

    report("[1/2] Checking TLS and rejection of missing Actions credentials...")
    response = requester(origin, ACTION_PATH, "POST", None, REQUEST_BODY)
    _expect_status(response, {401}, "unauthenticated Actions request")

    report("[2/2] Calling an authenticated read-only Joplin Action...")
    response = requester(origin, ACTION_PATH, "POST", token, REQUEST_BODY)
    _expect_status(response, {200}, "authenticated Actions request")
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SetupError("authenticated Actions response is not valid JSON") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SetupError("authenticated Actions response does not report success: true")
    if not isinstance(payload.get("result"), dict) or not isinstance(
        payload.get("request_id"), str
    ):
        raise SetupError("authenticated Actions response has an invalid success envelope")


def generate_contract(origin: str, output: Path) -> int:
    """Generate, validate, and atomically write the canonical OpenAPI contract."""

    document = generate_openapi(registry_for_export(), origin)
    if document.get("openapi") != "3.1.0":
        raise SetupError("generated contract is not OpenAPI 3.1.0")
    if document.get("servers") != [{"url": origin}]:
        raise SetupError("generated contract contains the wrong public origin")
    paths = document.get("paths")
    if not isinstance(paths, dict) or ACTION_PATH not in paths:
        raise SetupError("generated contract does not contain the verified Action")

    operations = []
    for item in paths.values():
        if not isinstance(item, dict) or not isinstance(item.get("post"), dict):
            raise SetupError("generated contract contains an invalid Action operation")
        operations.append(item["post"])
    if not operations or any(
        operation.get("security") != [{"GPTActionBearer": []}]
        for operation in operations
    ):
        raise SetupError("generated contract does not require bearer authentication")

    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_file_atomic(output, rendered)
    return len(operations)


def run_setup(
    host: str,
    token: str,
    *,
    output: Path = OUTPUT,
    requester: Requester = https_request,
    report: Reporter = print,
) -> tuple[str, int]:
    """Validate operator input, verify the endpoint, and create the contract."""

    origin = normalize_origin(host)
    try:
        validate_bearer_token(token, label="GPT Actions")
    except ValueError as exc:
        raise SetupError(str(exc)) from None
    verify_public_endpoint(origin, token, requester=requester, report=report)
    report("[3/3] Generating the OpenAPI contract...")
    operation_count = generate_contract(origin, output)
    return origin, operation_count


def main() -> int:
    print("This assistant sends the token only to the entered HTTPS origin.")
    print("The token is not displayed, logged, written to disk, or passed as an argument.")
    try:
        host = input("Public Actions host (for example notes.example.com): ")
        origin = normalize_origin(host)
        print(f"Actions origin: {origin}")
        token = getpass.getpass("GPT Actions bearer token (input hidden): ")
        origin, operation_count = run_setup(origin, token)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.", file=sys.stderr)
        return 130
    except (OSError, SetupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Endpoint verified: {origin}")
    print(f"OpenAPI contract: {OUTPUT}")
    print(f"Detected Actions: {operation_count}")
    print("Paste that JSON file into the ChatGPT Action schema editor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
