#!/usr/bin/env python3
"""Tell IndexNow search engines which documentation pages changed.

`plan` runs before publication: it compares the built sitemap with the
published one and records new, changed and removed URLs. `submit` runs after
publication: it waits until the site serves the planned sitemap and the key
file, then sends the URLs to IndexNow, which shares them with Bing, Yandex,
Seznam, Naver and other participants.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ENDPOINT = "https://api.indexnow.org/indexnow"
USER_AGENT = "joplin-md-sync-indexnow/1 (+https://github.com/kogeler/joplin-md-sync)"
NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
URLS_FILE = "urls.txt"
SITEMAP_FILE = "sitemap.xml"
MAX_URLS = 10_000


class IndexNowError(RuntimeError):
    """Raised when a plan or submission cannot be completed safely."""


def sitemap_dates(document: bytes) -> dict[str, str]:
    """Map each sitemap URL to its lastmod value."""

    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise IndexNowError("sitemap is not valid XML") from exc
    return {
        url.findtext("s:loc", "", NAMESPACE).strip(): url.findtext(
            "s:lastmod", "", NAMESPACE
        ).strip()
        for url in root.findall("s:url", NAMESPACE)
    }


def changed_urls(published: bytes | None, built: bytes) -> list[str]:
    """New or re-dated URLs of the built sitemap plus URLs it no longer lists."""

    new = sitemap_dates(built)
    if published is None:
        return sorted(new)
    old = sitemap_dates(published)
    changed = {url for url, lastmod in new.items() if old.get(url) != lastmod}
    return sorted(changed | (set(old) - set(new)))


def fetch(url: str) -> bytes | None:
    """Return the body of a public URL, or None when it does not exist."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise IndexNowError(f"GET {url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IndexNowError(f"GET {url} failed: {exc.reason}") from exc


def plan(site_dir: Path, site_url: str, output_dir: Path, *, every_page: bool) -> list[str]:
    built = (site_dir / SITEMAP_FILE).read_bytes()
    published = None if every_page else fetch(urljoin(site_url, SITEMAP_FILE))
    urls = changed_urls(published, built)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / SITEMAP_FILE).write_bytes(built)
    (output_dir / URLS_FILE).write_text("".join(f"{url}\n" for url in urls), encoding="utf-8")
    return urls


def payload(site_url: str, key: str, urls: list[str]) -> dict[str, object]:
    host = urlsplit(site_url).hostname
    foreign = [url for url in urls if urlsplit(url).hostname != host]
    if foreign:
        raise IndexNowError(f"URLs outside {host}: {foreign}")
    if len(urls) > MAX_URLS:
        raise IndexNowError(f"IndexNow accepts at most {MAX_URLS} URLs per request")
    return {
        "host": host,
        "key": key,
        "keyLocation": urljoin(site_url, f"{key}.txt"),
        "urlList": urls,
    }


def wait_for_publication(site_url: str, key: str, sitemap: bytes, timeout: float) -> None:
    """Wait until the public site serves this build's sitemap and the key file."""

    deadline = time.monotonic() + timeout
    while True:
        served_key = fetch(urljoin(site_url, f"{key}.txt"))
        if fetch(urljoin(site_url, SITEMAP_FILE)) == sitemap and served_key is not None:
            if served_key.decode("utf-8", "replace").strip() != key:
                raise IndexNowError("the published key file does not hold the IndexNow key")
            return
        if time.monotonic() >= deadline:
            raise IndexNowError("the site still serves an older build; submit again later")
        time.sleep(30)


def submit(site_url: str, key: str, plan_dir: Path, timeout: float) -> int:
    urls = [line for line in (plan_dir / URLS_FILE).read_text(encoding="utf-8").split() if line]
    if not urls:
        print("IndexNow: no changed pages to submit")
        return 0
    body = json.dumps(payload(site_url, key, urls)).encode()
    wait_for_publication(site_url, key, (plan_dir / SITEMAP_FILE).read_bytes(), timeout)
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        raise IndexNowError(f"IndexNow rejected the submission with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IndexNowError(f"IndexNow request failed: {exc.reason}") from exc
    # 202 means the key is still being validated, which is normal on first use.
    if status not in (200, 202):
        raise IndexNowError(f"IndexNow answered HTTP {status}")
    print(f"IndexNow accepted {len(urls)} URLs (HTTP {status})")
    return len(urls)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="list pages that differ from the published site")
    plan_parser.add_argument("--site-dir", type=Path, required=True)
    plan_parser.add_argument("--site-url", required=True)
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    plan_parser.add_argument("--all", action="store_true", help="plan every page")
    submit_parser = commands.add_parser("submit", help="send a plan after publication")
    submit_parser.add_argument("--site-url", required=True)
    submit_parser.add_argument("--key-file", type=Path, required=True)
    submit_parser.add_argument("--plan-dir", type=Path, required=True)
    submit_parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            urls = plan(args.site_dir, args.site_url, args.output_dir, every_page=args.all)
            print(f"IndexNow plan: {len(urls)} changed URLs")
        else:
            key = args.key_file.read_text(encoding="utf-8").strip()
            submit(args.site_url, key, args.plan_dir, args.timeout)
    except (IndexNowError, OSError) as exc:
        print(f"IndexNow failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
