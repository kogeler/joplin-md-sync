"""MkDocs hooks for repository links and canonical root site artifacts."""

import re
import shutil
from pathlib import Path
from typing import Any

REPO_BLOB = "https://github.com/kogeler/joplin-md-sync/blob/main"
_LINK_PATTERN = re.compile(r"\]\((?P<target>(?:\.\./)+[^)#]+)(?P<fragment>#[^)]*)?\)")


def on_config(config: Any, **_kwargs: object) -> Any:
    """Disable analytics when no measurement property was configured."""

    extra = config.get("extra", {})
    analytics = extra.get("analytics", {})
    property_value = analytics.get("property")
    if not isinstance(property_value, str) or not property_value.strip():
        extra.pop("analytics", None)
    return config


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_kwargs: object) -> str:
    """Rewrite links that leave docs_dir to stable repository blob links."""

    source_dir = Path(page.file.abs_src_path).parent
    docs_dir = Path(config["docs_dir"]).resolve()
    repository = docs_dir.parent

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


def on_post_build(*, config: Any, **_kwargs: object) -> None:
    """Publish non-page site inputs at canonical root URLs."""

    docs_dir = Path(config["docs_dir"])
    site_dir = Path(config["site_dir"])
    for name in ("CNAME", "llms.txt", "robots.txt"):
        shutil.copyfile(docs_dir / "site" / name, site_dir / name)
