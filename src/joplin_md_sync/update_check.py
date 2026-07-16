"""Version freshness check against GitHub Releases.

The tool never updates itself; it only reports and prints the exact
recommended update command.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from joplin_md_sync import REPOSITORY_URL, __version__
from joplin_md_sync.errors import ApiError

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.match(tag.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _repo_slug() -> str:
    return REPOSITORY_URL.removeprefix("https://github.com/").strip("/")


def _fetch_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "joplin-md-sync"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    *, include_prerelease: bool = False, timeout: float = 10.0
) -> dict[str, Any]:
    """Return {current, latest, outdated, update_command, ...}.

    Raises ApiError (code UPDATE_CHECK_FAILED) when GitHub cannot be reached
    or no stable release exists yet.
    """
    slug = _repo_slug()
    try:
        if include_prerelease:
            releases = _fetch_json(
                f"https://api.github.com/repos/{slug}/releases?per_page=20", timeout
            )
            candidates = [r for r in releases if not r.get("draft")]
        else:
            candidates = [_fetch_json(f"https://api.github.com/repos/{slug}/releases/latest", timeout)]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        raise ApiError(
            f"update check could not be completed: {exc}", code="UPDATE_CHECK_FAILED"
        ) from exc

    latest_tag: str | None = None
    latest_ver: tuple[int, int, int] | None = None
    for release in candidates:
        ver = parse_version(release.get("tag_name", ""))
        if ver is None:
            continue
        if latest_ver is None or ver > latest_ver:
            latest_ver, latest_tag = ver, release["tag_name"]
    if latest_ver is None or latest_tag is None:
        raise ApiError(
            "update check could not be completed: no stable releases found",
            code="UPDATE_CHECK_FAILED",
        )

    current = parse_version(__version__)
    outdated = current is not None and latest_ver > current
    return {
        "current_version": __version__,
        "latest_version": latest_tag.lstrip("v"),
        "latest_tag": latest_tag,
        "outdated": outdated,
        "include_prerelease": include_prerelease,
        "repository": REPOSITORY_URL,
        "update_command": (
            f'python -m pip install --upgrade "git+{REPOSITORY_URL}.git@{latest_tag}"'
        ),
    }
