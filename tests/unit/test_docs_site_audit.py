"""Tests for generated documentation output auditing."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from scripts.audit_docs_site import AuditError, audit_site

SITE_URL = "https://joplin-mcp.romancello.net/"
GUIDE_URL = f"{SITE_URL}guide/"
INDEXNOW_KEY = "0123456789abcdef"
STRUCTURED_DATA = (
    '<script type="application/ld+json">'
    '{"@context": "https://schema.org", "@type": "SoftwareSourceCode"}</script>'
)


def _page(canonical: str, title: str, description: str, extra: str = "", body: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <title>{title}</title>
    <meta name="description" content="{description}">
    <link rel="canonical" href="{canonical}">
    <link rel="stylesheet" href="/assets/site.css">
    {extra}
  </head>
  <body><h1 id="start">{title}</h1>{body}</body>
</html>
"""


def _write_site(
    root: Path,
    *,
    body: str = '<a href="#start">Start</a>',
    guide_description: str = "How to use the guide.",
    structured_data: str = STRUCTURED_DATA,
) -> None:
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("body { color: black; }\n", encoding="utf-8")
    (root / "index.html").write_text(
        _page(SITE_URL, "Documentation", "What the project does.", structured_data, body),
        encoding="utf-8",
    )
    (root / "guide").mkdir()
    (root / "guide" / "index.html").write_text(
        _page(GUIDE_URL, "Guide", guide_description), encoding="utf-8"
    )
    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE_URL}</loc><lastmod>2026-11-15T09:01:38+02:00</lastmod></url>
  <url><loc>{GUIDE_URL}</loc><lastmod>2026-08-21T13:29:03+03:00</lastmod></url>
</urlset>
"""
    (root / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    with gzip.open(root / "sitemap.xml.gz", "wb") as stream:
        stream.write(sitemap.encode())
    (root / "CNAME").write_text("joplin-mcp.romancello.net\n", encoding="utf-8")
    (root / "llms.txt").write_text(
        f"# joplin-md-sync\n\n- [Home]({SITE_URL})\n- [Guide]({GUIDE_URL})\n", encoding="utf-8"
    )
    (root / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY, encoding="utf-8")
    (root / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
        encoding="utf-8",
    )


def test_generated_site_audit_accepts_complete_output(tmp_path: Path) -> None:
    _write_site(tmp_path)

    assert audit_site(tmp_path, SITE_URL) == (2, 5)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('<a href="missing/">Missing</a>', "target does not exist"),
        ('<a href="#missing">Missing</a>', "missing anchor"),
        ('<img src="assets/missing.png">', "missing an alt attribute"),
    ],
)
def test_generated_site_audit_rejects_invalid_output(
    tmp_path: Path, body: str, message: str
) -> None:
    _write_site(tmp_path, body=body)

    with pytest.raises(AuditError, match=message):
        audit_site(tmp_path, SITE_URL)


def test_generated_site_audit_rejects_incorrect_robots_sitemap(tmp_path: Path) -> None:
    _write_site(tmp_path)
    (tmp_path / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: https://example.invalid/sitemap.xml\n",
        encoding="utf-8",
    )

    with pytest.raises(AuditError, match="does not advertise"):
        audit_site(tmp_path, SITE_URL)


def test_generated_site_audit_rejects_a_shared_description(tmp_path: Path) -> None:
    _write_site(tmp_path, guide_description="What the project does.")

    with pytest.raises(AuditError, match="description duplicates /"):
        audit_site(tmp_path, SITE_URL)


def test_generated_site_audit_rejects_missing_structured_data(tmp_path: Path) -> None:
    _write_site(tmp_path, structured_data="")

    with pytest.raises(AuditError, match="no JSON-LD"):
        audit_site(tmp_path, SITE_URL)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (
            lambda root: (root / "llms.txt").write_text("# joplin-md-sync\n"),
            "llms.txt does not list",
        ),
        (lambda root: (root / f"{INDEXNOW_KEY}.txt").write_text("other-key\n"), "IndexNow key"),
        (
            lambda root: (root / "sitemap.xml").write_text(
                (root / "sitemap.xml")
                .read_text()
                .replace("<lastmod>2026-08-21T13:29:03+03:00</lastmod>", "")
            ),
            "lastmod for https://joplin-mcp.romancello.net/guide/",
        ),
    ],
)
def test_generated_site_audit_rejects_incomplete_crawler_files(
    tmp_path: Path, damage: object, message: str
) -> None:
    _write_site(tmp_path)
    damage(tmp_path)  # type: ignore[operator]

    with pytest.raises(AuditError, match=message):
        audit_site(tmp_path, SITE_URL)
