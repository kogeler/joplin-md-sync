"""MkDocs build hook for links from published docs to repository-root files."""

import re

REPO_BLOB = "https://github.com/kogeler/joplin-md-sync/blob/main"
_LINK_PATTERN = re.compile(r"\]\(\.\./([^)]+)\)")


def on_page_markdown(markdown: str, **_kwargs: object) -> str:
    """Keep links to AGENTS.md, examples, and policies valid on the docs site."""

    return _LINK_PATTERN.sub(rf"]({REPO_BLOB}/\1)", markdown)
