"""MkDocs hooks for repository links, page metadata and root site artifacts."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_BLOB = "https://github.com/kogeler/joplin-md-sync/blob/main"
_LINK_PATTERN = re.compile(r"\]\((?P<target>(?:\.\./)+[^)#]+)(?P<fragment>#[^)]*)?\)")
# Material writes the description into an HTML attribute without escaping it.
_UNSAFE_META = re.compile(r"[\"<>&]")
DESCRIPTION_LIMIT = 160
INDEXNOW_KEY = re.compile(r"[A-Za-z0-9-]{8,128}")
LLMS_PAGES_MARKER = "<!-- pages -->"

_nav: Any = None


class SiteInputError(ValueError):
    """A documentation source or build input cannot be published as is."""


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repository(config: Any) -> Path:
    return Path(config["docs_dir"]).resolve().parent


def on_config(config: Any, **_kwargs: object) -> Any:
    """Disable empty analytics and require the history behind page dates."""

    extra = config.get("extra", {})
    analytics = extra.get("analytics", {})
    property_value = analytics.get("property")
    if not isinstance(property_value, str) or not property_value.strip():
        extra.pop("analytics", None)
    try:
        shallow = _git(_repository(config), "rev-parse", "--is-shallow-repository")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SiteInputError("sitemap dates need the documentation's Git history") from exc
    if shallow != "false":
        raise SiteInputError("sitemap dates need full Git history; check out with fetch-depth: 0")
    return config


def on_nav(nav: Any, **_kwargs: object) -> Any:
    """Keep the navigation for the generated llms.txt index."""

    global _nav
    _nav = nav
    return nav


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_kwargs: object) -> str:
    """Check page metadata, date the page by Git and fix repository links."""

    source = page.file.src_uri
    for name in ("title", "description"):
        value = page.meta.get(name)
        if not isinstance(value, str) or not value.strip():
            raise SiteInputError(f"{source}: front matter needs a {name}")
        if _UNSAFE_META.search(value):
            raise SiteInputError(f"{source}: {name} must not contain quotes, <, > or &")
    if len(page.meta["description"]) > DESCRIPTION_LIMIT:
        raise SiteInputError(f"{source}: description exceeds {DESCRIPTION_LIMIT} characters")

    repository = _repository(config)
    # The last commit that touched the source; uncommitted pages keep the build date.
    if changed := _git(repository, "log", "-1", "--format=%cI", "--", page.file.abs_src_path):
        page.update_date = changed

    source_dir = Path(page.file.abs_src_path).parent
    docs_dir = Path(config["docs_dir"]).resolve()

    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        resolved = (source_dir / target).resolve()
        try:
            resolved.relative_to(docs_dir)
        except ValueError:
            repository_path = resolved.relative_to(repository).as_posix()
            return f"]({REPO_BLOB}/{repository_path}{match.group('fragment') or ''})"
        return match.group(0)

    return _LINK_PATTERN.sub(replace, markdown)


def _llms_entry(item: Any) -> str:
    if item.is_page:
        return f"- [{item.title}]({item.canonical_url}): {item.meta['description']}"
    return f"- [{item.title}]({item.url})"


def _llms_section(items: list[Any]) -> list[str]:
    lines = []
    for item in items:
        lines.extend(_llms_section(item.children) if item.is_section else [_llms_entry(item)])
    return lines


def _llms_index(nav: Any) -> str:
    loose = [item for item in nav.items if not item.is_section]
    blocks = ["## Start here\n\n" + "\n".join(_llms_section(loose))]
    blocks.extend(
        f"## {item.title}\n\n" + "\n".join(_llms_section(item.children))
        for item in nav.items
        if item.is_section
    )
    return "\n\n".join(blocks)


def on_post_build(*, config: Any, **_kwargs: object) -> None:
    """Publish crawler files at canonical root URLs."""

    inputs = Path(config["docs_dir"]) / "site"
    site_dir = Path(config["site_dir"])
    for name in ("CNAME", "robots.txt"):
        shutil.copyfile(inputs / name, site_dir / name)

    template = (inputs / "llms.txt").read_text(encoding="utf-8")
    if template.count(LLMS_PAGES_MARKER) != 1:
        raise SiteInputError(f"llms.txt needs exactly one {LLMS_PAGES_MARKER} line")
    llms = template.replace(LLMS_PAGES_MARKER, _llms_index(_nav))
    (site_dir / "llms.txt").write_text(llms, encoding="utf-8")

    # IndexNow verifies ownership by fetching /<key>.txt; the key is public by design.
    key = (inputs / "indexnow-key.txt").read_text(encoding="utf-8").strip()
    if not INDEXNOW_KEY.fullmatch(key):
        raise SiteInputError("indexnow-key.txt must hold 8 to 128 letters, digits or dashes")
    (site_dir / f"{key}.txt").write_text(key, encoding="utf-8")
