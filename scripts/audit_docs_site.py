#!/usr/bin/env python3
"""Audit the generated documentation site without external network access."""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


class AuditError(RuntimeError):
    """Raised when generated site output violates the publication contract."""


@dataclass
class HtmlDocument:
    """Generated HTML facts used by the offline audit."""

    ids: set[str] = field(default_factory=set)
    references: list[tuple[str, str]] = field(default_factory=list)
    canonicals: list[str] = field(default_factory=list)
    h1_count: int = 0
    title_text: list[str] = field(default_factory=list)
    in_title: bool = False


class SiteHtmlParser(HTMLParser):
    """Collect navigation, asset, anchor, and metadata references."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document = HtmlDocument()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if identifier := values.get("id"):
            self.document.ids.add(identifier)
        if tag == "h1":
            self.document.h1_count += 1
        if tag == "title":
            self.document.in_title = True
        if tag == "img" and "alt" not in values:
            raise AuditError("generated image is missing an alt attribute")
        for attribute in ("href", "src"):
            if target := values.get(attribute):
                self.document.references.append((tag, target))
        if tag == "link" and "canonical" in (values.get("rel") or "").split():
            if canonical := values.get("href"):
                self.document.canonicals.append(canonical)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.document.in_title = False

    def handle_data(self, data: str) -> None:
        if self.document.in_title:
            self.document.title_text.append(data)


def _route_for_page(path: Path, site_dir: Path) -> str:
    relative = path.relative_to(site_dir)
    if relative.name == "index.html":
        parent = relative.parent.as_posix().strip(".")
        return f"/{parent}/" if parent else "/"
    return f"/{relative.as_posix()}"


def _local_target(site_dir: Path, path: str) -> Path:
    relative = unquote(path).lstrip("/")
    candidate = site_dir / relative
    if path.endswith("/"):
        candidate /= "index.html"
    resolved = candidate.resolve()
    try:
        resolved.relative_to(site_dir.resolve())
    except ValueError as exc:
        raise AuditError(f"site reference escapes output directory: {path}") from exc
    return resolved


def _parse_document(path: Path) -> HtmlDocument:
    parser = SiteHtmlParser()
    try:
        parser.feed(path.read_text(encoding="utf-8"))
    except AuditError as exc:
        raise AuditError(f"{path}: {exc}") from exc
    return parser.document


def _sitemap_urls(path: Path) -> set[str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise AuditError(f"invalid sitemap: {path}") from exc
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return {
        value
        for element in root.findall("s:url/s:loc", namespace)
        if (value := (element.text or "").strip())
    }


def audit_site(site_dir: Path, site_url: str) -> tuple[int, int]:
    """Validate generated pages and return page/reference counts."""

    site_dir = site_dir.resolve()
    origin = site_url.rstrip("/") + "/"
    origin_parts = urlsplit(origin)
    pages = {
        _route_for_page(path, site_dir): path
        for path in sorted(site_dir.rglob("*.html"))
        if path.name != "404.html"
    }
    if not pages or "/" not in pages:
        raise AuditError("generated site has no root page")

    documents = {route: _parse_document(path) for route, path in pages.items()}
    expected_urls = {urljoin(origin, route.lstrip("/")) for route in pages}
    sitemap = site_dir / "sitemap.xml"
    if not sitemap.is_file() or not (site_dir / "sitemap.xml.gz").is_file():
        raise AuditError("sitemap.xml and sitemap.xml.gz must both exist")
    actual_urls = _sitemap_urls(sitemap)
    if actual_urls != expected_urls:
        missing = sorted(expected_urls - actual_urls)
        extra = sorted(actual_urls - expected_urls)
        raise AuditError(f"sitemap inventory mismatch; missing={missing}, extra={extra}")

    expected_host = origin_parts.hostname or ""
    cname = (site_dir / "CNAME").read_text(encoding="utf-8").strip()
    if cname != expected_host:
        raise AuditError(f"CNAME {cname!r} does not match {expected_host!r}")
    if not (site_dir / "llms.txt").read_text(encoding="utf-8").strip():
        raise AuditError("llms.txt is missing or empty")
    robots = (site_dir / "robots.txt").read_text(encoding="utf-8")
    robots_lines = {line.strip() for line in robots.splitlines() if line.strip()}
    sitemap_directive = f"Sitemap: {urljoin(origin, 'sitemap.xml')}"
    if not {"User-agent: *", "Allow: /"}.issubset(robots_lines):
        raise AuditError("robots.txt does not explicitly allow crawling")
    if sitemap_directive not in robots_lines:
        raise AuditError(f"robots.txt does not advertise {sitemap_directive}")

    checked_references = 0
    for route, page in pages.items():
        document = documents[route]
        expected_canonical = urljoin(origin, route.lstrip("/"))
        if document.canonicals != [expected_canonical]:
            raise AuditError(
                f"{route}: canonical {document.canonicals!r} != {expected_canonical!r}"
            )
        if document.h1_count != 1:
            raise AuditError(f"{route}: expected one h1, found {document.h1_count}")
        if not "".join(document.title_text).strip():
            raise AuditError(f"{route}: empty title")

        html = page.read_text(encoding="utf-8")
        if re.search(r"googletagmanager\.com/gtag/js\?id=[\"'&<]", html):
            raise AuditError(f"{route}: Google Analytics has an empty property")

        page_url = urljoin(origin, route.lstrip("/"))
        for tag, reference in document.references:
            if reference.startswith(("data:", "javascript:", "mailto:", "tel:")):
                continue
            target_url = urlsplit(urljoin(page_url, reference))
            if target_url.scheme not in ("http", "https"):
                continue
            if (target_url.scheme, target_url.netloc) != (
                origin_parts.scheme,
                origin_parts.netloc,
            ):
                continue
            checked_references += 1
            target = _local_target(site_dir, target_url.path)
            if not target.is_file():
                raise AuditError(f"{route}: {tag} target does not exist: {reference}")
            if target_url.fragment and target.suffix == ".html":
                target_route = _route_for_page(target, site_dir)
                target_document = documents.get(target_route) or _parse_document(target)
                fragment = unquote(target_url.fragment)
                if fragment not in target_document.ids:
                    raise AuditError(f"{route}: missing anchor in {reference}")

    return len(pages), checked_references


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--site-url", default="https://joplin-mcp.romancello.net/")
    args = parser.parse_args()
    try:
        pages, references = audit_site(args.site_dir, args.site_url)
    except (AuditError, OSError) as exc:
        print(f"documentation site audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"documentation site audit passed: {pages} pages, {references} local references")


if __name__ == "__main__":
    main()
