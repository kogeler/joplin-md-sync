"""Traceability and ownership checks for the documentation contract catalog."""

from __future__ import annotations

import argparse
import ast
import re
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"
CONTRACTS = DOCS / "contracts"
CONTRACT_FILES = {
    "AGENT_INTERFACES.md",
    "CI_RELEASES.md",
    "CLI.md",
    "DEPENDENCIES.md",
    "README.md",
    "SECURITY.md",
    "SERVICE.md",
    "SYNCHRONIZATION.md",
    "WORKSPACE.md",
}
CONTRACT_PREFIXES = {
    "AGENT_INTERFACES.md": "AIF",
    "CI_RELEASES.md": "CIR",
    "CLI.md": "CLI",
    "DEPENDENCIES.md": "DEP",
    "SECURITY.md": "SEC",
    "SERVICE.md": "SVC",
    "SYNCHRONIZATION.md": "SYN",
    "WORKSPACE.md": "WSP",
}
USER_FILES = {
    "AGENT_INTERFACES.md",
    "AGENT_WORKFLOWS.md",
    "CHATGPT_ACTIONS.md",
    "CLI.md",
    "CONFLICTS.md",
    "GETTING_STARTED.md",
    "MCP_API.md",
    "OVERVIEW.md",
    "SELF_HOSTED.md",
    "SERVICE.md",
    "WORKSPACE_FORMAT.md",
}
MAINTENANCE_FILES = {
    "ARCHITECTURE.md",
    "DEPENDENCIES.md",
    "DEVELOPMENT.md",
    "RELEASES.md",
    "SECURITY.md",
    "SERVICE.md",
    "SYNCHRONIZATION.md",
}
ASSERTION = re.compile(r"^### `([A-Z]{3}-[0-9]{3})` - .+$", re.MULTILINE)
EVIDENCE = re.compile(r"^- \[`[^`]+`\]\(([^)]+)\) - `([^`]+)`$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")


def _node_exists(path: Path, node_id: str) -> bool:
    parts = node_id.split("::")
    if Path(parts[0]) != path.relative_to(ROOT) or len(parts) < 2:
        return False
    body: list[ast.stmt] = ast.parse(path.read_text(encoding="utf-8")).body
    for name in parts[1:]:
        match = next(
            (
                node
                for node in body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ),
            None,
        )
        if match is None:
            return False
        body = match.body if isinstance(match, ast.ClassDef) else []
    return True


def test_contract_assertions_have_unique_ids_and_real_evidence() -> None:
    seen: set[str] = set()
    for path in sorted(CONTRACTS.glob("*.md")):
        if path.name == "README.md":
            continue
        content = path.read_text(encoding="utf-8")
        matches = list(ASSERTION.finditer(content))
        assert matches, path
        prefix = CONTRACT_PREFIXES[path.name]
        assert [match.group(1) for match in matches] == [
            f"{prefix}-{number:03d}" for number in range(1, len(matches) + 1)
        ]
        for index, match in enumerate(matches):
            assertion_id = match.group(1)
            assert assertion_id not in seen, assertion_id
            seen.add(assertion_id)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            section = content[match.end() : end]
            assert "\n**Contract:** " in section, assertion_id
            assert "\n**Evidence:**\n" in section, assertion_id
            evidence = EVIDENCE.findall(section)
            assert evidence, assertion_id
            for relative_link, node_id in evidence:
                target = (path.parent / relative_link).resolve()
                assert target.is_file(), (assertion_id, relative_link)
                assert target == (ROOT / node_id.split("::", 1)[0]).resolve(), (
                    assertion_id,
                    relative_link,
                    node_id,
                )
                assert _node_exists(target, node_id), (assertion_id, node_id)


def test_documentation_tree_and_site_navigation_are_complete() -> None:
    assert {path.name for path in CONTRACTS.glob("*.md")} == CONTRACT_FILES
    assert {path.name for path in (DOCS / "user").glob("*.md")} == USER_FILES
    assert {path.name for path in (DOCS / "maintenance").glob("*.md")} == MAINTENANCE_FILES
    assert {path.name for path in DOCS.glob("*.md")} == {"index.md"}
    assert (DOCS / "site" / "CNAME").read_text(encoding="utf-8").strip() == (
        "joplin-mcp.romancello.net"
    )
    assert (DOCS / "site" / "llms.txt").is_file()
    assert (DOCS / "site" / "robots.txt").read_text(encoding="utf-8") == (
        "User-agent: *\nAllow: /\n\nSitemap: https://joplin-mcp.romancello.net/sitemap.xml\n"
    )
    assert (DOCS / "site" / "assets" / "stylesheets" / "extra.css").is_file()
    assert (DOCS / "site" / "overrides" / "home.html").is_file()

    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "site_url: https://joplin-mcp.romancello.net/" in mkdocs
    for directory, names in (
        ("contracts", CONTRACT_FILES - {"README.md"}),
        ("user", USER_FILES),
        ("maintenance", MAINTENANCE_FILES),
    ):
        for name in names:
            assert f"{directory}/{name}" in mkdocs
    assert "site/assets/stylesheets/extra.css" in mkdocs
    assert "site/assets/javascripts/site.js" in mkdocs
    assert "docs/site/overrides" in mkdocs
    excluded = mkdocs.split("exclude_docs:", 1)[1].split("markdown_extensions:", 1)[0]
    assert "site/hooks.py" in excluded
    assert "site/robots.txt" in excluded
    assert "site/__pycache__/**" in excluded

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "DOCS_SITE_URL := https://joplin-mcp.romancello.net/" in makefile
    assert "<loc>$(DOCS_SITE_URL)</loc>" in makefile
    assert "docs-audit: docs-build" in makefile
    assert "docs-screenshots: docs-audit" in makefile

    normative_words = re.compile(r"\b(?:MUST|MUST NOT|SHOULD|SHOULD NOT|MAY)\b")
    for directory in (DOCS / "user", DOCS / "maintenance"):
        for path in directory.glob("*.md"):
            assert normative_words.search(path.read_text(encoding="utf-8")) is None, path


def _public_options(parser: argparse.ArgumentParser) -> set[str]:
    options = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                options.update(_public_options(child))
    return options


def test_cli_reference_covers_every_public_option() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from joplin_md_sync.cli import build_parser
    finally:
        sys.path.pop(0)

    reference = "\n".join(
        (DOCS / "user" / name).read_text(encoding="utf-8") for name in ("CLI.md", "CONFLICTS.md")
    )
    missing = {
        option for option in _public_options(build_parser()) if f"`{option}" not in reference
    }
    assert missing == set()


def _heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip().replace("`", "")
        slug = re.sub(r"[^\w\s-]", "", heading.lower())
        anchors.add(re.sub(r"[-\s]+", "-", slug).strip("-"))
    return anchors


def test_all_relative_documentation_links_and_home_routes_resolve() -> None:
    for path in DOCS.rglob("*.md"):
        for raw_target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
            target = raw_target.split(maxsplit=1)[0]
            if target.startswith(("#", ":/", "http://", "https://", "mailto:")):
                continue
            relative, _, fragment = target.partition("#")
            resolved = (path.parent / relative).resolve()
            assert resolved.is_file(), (path, target)
            if fragment and resolved.suffix == ".md":
                assert fragment in _heading_anchors(resolved), (path, target)

    homepage = (DOCS / "index.md").read_text(encoding="utf-8")
    for route in re.findall(r'href="([^"#]+)"', homepage):
        if route.startswith(("http://", "https://")):
            continue
        relative = route.rstrip("/")
        candidate = DOCS / f"{relative}.md"
        if relative == "contracts":
            candidate = CONTRACTS / "README.md"
        assert candidate.is_file(), route


def test_homepage_leads_with_chatgpt_mcp_and_headless_deployment() -> None:
    homepage = (DOCS / "index.md").read_text(encoding="utf-8")
    assert "<h1 data-reveal>Joplin for ChatGPT &amp; MCP</h1>" in homepage
    chatgpt = homepage.index("Connect ChatGPT")
    local_mcp = homepage.index("Connect local MCP")
    deploy = homepage.index("Deploy headless")
    headless = homepage.index("Deploy the complete headless path")
    markdown = homepage.index("Use ordinary Markdown when the diff matters")
    assert chatgpt < local_mcp < deploy < headless < markdown
    assert 'href="user/MCP_API/#local-stdio"' in homepage
    assert "local stdio MCP" in homepage.partition("</section>")[0]
    assert "install_joplin_terminal.py" in homepage

    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    social_metadata = (DOCS / "site" / "overrides" / "home.html").read_text(
        encoding="utf-8"
    )
    assert '"MCP: local & HTTP": user/MCP_API.md' in mkdocs
    assert "local stdio MCP" in mkdocs
    assert "local stdio MCP" in social_metadata


def test_site_hook_rewrites_repository_links_and_publishes_root_files(
    tmp_path: Path,
) -> None:
    hooks = runpy.run_path(DOCS / "site" / "hooks.py")
    configure = cast("Any", hooks["on_config"])
    rewrite = cast("Any", hooks["on_page_markdown"])
    publish = cast("Any", hooks["on_post_build"])
    page = SimpleNamespace(file=SimpleNamespace(abs_src_path=DOCS / "user" / "CLI.md"))
    config = {"docs_dir": str(DOCS), "site_dir": str(tmp_path)}

    missing_analytics = {"extra": {"analytics": {"provider": "google", "property": None}}}
    empty_analytics = {"extra": {"analytics": {"provider": "google", "property": "  "}}}
    configured_analytics = {"extra": {"analytics": {"provider": "google", "property": "G-TEST123"}}}
    assert "analytics" not in configure(missing_analytics)["extra"]
    assert "analytics" not in configure(empty_analytics)["extra"]
    assert configure(configured_analytics)["extra"]["analytics"]["property"] == "G-TEST123"

    rendered = rewrite(
        "[readme](../../README.md#installation) [contract](../contracts/CLI.md)",
        page=page,
        config=config,
    )
    assert (
        "[readme](https://github.com/kogeler/joplin-md-sync/blob/main/README.md#installation)"
        in rendered
    )
    assert "[contract](../contracts/CLI.md)" in rendered

    publish(config=config)
    assert (tmp_path / "CNAME").read_text(encoding="utf-8") == (DOCS / "site" / "CNAME").read_text(
        encoding="utf-8"
    )
    assert (tmp_path / "llms.txt").read_text(encoding="utf-8") == (
        DOCS / "site" / "llms.txt"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "robots.txt").read_text(encoding="utf-8") == (
        DOCS / "site" / "robots.txt"
    ).read_text(encoding="utf-8")
