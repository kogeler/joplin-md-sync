"""Tests for generated documentation output auditing."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from scripts.audit_docs_site import AuditError, audit_site

SITE_URL = "https://joplin-mcp.romancello.net/"


def _write_site(root: Path, *, body: str = '<a href="#start">Start</a>') -> None:
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <title>Documentation</title>
    <link rel="canonical" href="{SITE_URL}">
    <link rel="stylesheet" href="assets/site.css">
  </head>
  <body><h1 id="start">Documentation</h1>{body}</body>
</html>
"""
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("body { color: black; }\n", encoding="utf-8")
    (root / "index.html").write_text(html, encoding="utf-8")
    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE_URL}</loc></url>
</urlset>
"""
    (root / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    with gzip.open(root / "sitemap.xml.gz", "wb") as stream:
        stream.write(sitemap.encode())
    (root / "CNAME").write_text("joplin-mcp.romancello.net\n", encoding="utf-8")
    (root / "llms.txt").write_text("# joplin-md-sync\n", encoding="utf-8")
    (root / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
        encoding="utf-8",
    )


def test_generated_site_audit_accepts_complete_output(tmp_path: Path) -> None:
    _write_site(tmp_path)

    assert audit_site(tmp_path, SITE_URL) == (1, 3)


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
