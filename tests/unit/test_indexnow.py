"""Tests for the IndexNow plan and submission without network access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import indexnow

SITE = "https://docs.example/"
KEY = "0123456789abcdef"


def _sitemap(entries: dict[str, str]) -> bytes:
    urls = "".join(
        f"<url><loc>{SITE}{path}</loc><lastmod>{lastmod}</lastmod></url>"
        for path, lastmod in entries.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    ).encode()


def test_plan_lists_new_redated_and_removed_pages_only() -> None:
    published = _sitemap({"": "2026-01-01", "kept/": "2026-01-01", "gone/": "2026-01-01"})
    built = _sitemap({"": "2026-02-01", "kept/": "2026-01-01", "new/": "2026-02-01"})

    assert indexnow.changed_urls(published, built) == [SITE, f"{SITE}gone/", f"{SITE}new/"]
    assert indexnow.changed_urls(None, built) == [SITE, f"{SITE}kept/", f"{SITE}new/"]


def test_payload_names_the_key_location_and_rejects_foreign_urls() -> None:
    assert indexnow.payload(SITE, KEY, [SITE]) == {
        "host": "docs.example",
        "key": KEY,
        "keyLocation": f"{SITE}{KEY}.txt",
        "urlList": [SITE],
    }
    with pytest.raises(indexnow.IndexNowError, match=r"outside docs\.example"):
        indexnow.payload(SITE, KEY, ["https://other.example/"])


class _Response:
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self.body, self.status = body, status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_submit_waits_for_publication_then_posts_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _sitemap({"": "2026-02-01"})
    (tmp_path / "sitemap.xml").write_bytes(built)
    (tmp_path / "urls.txt").write_text(f"{SITE}\n", encoding="utf-8")
    served = iter([_sitemap({"": "2026-01-01"}), built])
    posted: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        if request.get_method() == "POST":
            assert request.get_header("User-agent") == indexnow.USER_AGENT
            posted.append(json.loads(request.data))
            return _Response(status=202)
        if request.full_url.endswith(f"{KEY}.txt"):
            return _Response(KEY.encode())
        return _Response(next(served))

    monkeypatch.setattr(indexnow.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(indexnow.time, "sleep", lambda _seconds: None)

    assert indexnow.submit(SITE, KEY, tmp_path, timeout=60) == 1
    assert posted == [indexnow.payload(SITE, KEY, [SITE])]


def test_submit_without_changes_sends_nothing(tmp_path: Path) -> None:
    (tmp_path / "urls.txt").write_text("", encoding="utf-8")

    assert indexnow.submit(SITE, KEY, tmp_path, timeout=0) == 0
