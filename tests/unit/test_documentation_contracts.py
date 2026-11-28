"""Traceability and ownership checks for the documentation contract catalog."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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
    "CLAUDE_CONNECTOR.md",
    "AGENT_INTERFACES.md",
    "AGENT_WORKFLOWS.md",
    "CLI.md",
    "CONFLICTS.md",
    "GETTING_STARTED.md",
    "JOPLIN_HEADLESS_SERVICE.md",
    "MCP_API.md",
    "OVERVIEW.md",
    "SELF_HOSTED.md",
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
# Git history is not available to this check, so it pins what is already known:
# a retired ID stays retired, and no prefix ever ends below its highest ID so
# far, defined or retired. Raise the floors when adding assertions.
PERMANENTLY_RETIRED = {"AIF-006", "AIF-007", "SEC-006"}
HIGHEST_ASSIGNED = {
    "AIF": 10,
    "CIR": 12,
    "CLI": 8,
    "DEP": 10,
    "SEC": 10,
    "SVC": 10,
    "SYN": 9,
    "WSP": 6,
}
RETIRED = re.compile(r"^Retired IDs: (.+)\.$", re.MULTILINE)
CONTRACT_ID = re.compile(r"`([A-Z]{3}-[0-9]{3})`")
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


def _retired_ids() -> set[str]:
    (listed,) = RETIRED.findall((CONTRACTS / "README.md").read_text(encoding="utf-8"))
    retired = CONTRACT_ID.findall(listed)
    assert len(retired) == len(set(retired)), retired
    assert PERMANENTLY_RETIRED <= set(retired), PERMANENTLY_RETIRED - set(retired)
    unknown = {item for item in retired if item[:3] not in CONTRACT_PREFIXES.values()}
    assert not unknown, unknown
    return set(retired)


def test_contract_assertions_have_unique_ids_and_real_evidence() -> None:
    seen: set[str] = set()
    retired = _retired_ids()
    for path in sorted(CONTRACTS.glob("*.md")):
        if path.name == "README.md":
            continue
        content = path.read_text(encoding="utf-8")
        matches = list(ASSERTION.finditer(content))
        assert matches, path
        prefix = CONTRACT_PREFIXES[path.name]
        # Only listed retired IDs may leave a gap, and they are never reused.
        ids = [match.group(1) for match in matches]
        highest = max(
            int(assertion_id[4:])
            for assertion_id in (*ids, *retired)
            if assertion_id.startswith(prefix + "-")
        )
        # A deleted trailing assertion must be listed as retired, not vanish.
        assert highest >= HIGHEST_ASSIGNED[prefix], (prefix, highest)
        assert ids == [
            assertion_id
            for number in range(1, highest + 1)
            if (assertion_id := f"{prefix}-{number:03d}") not in retired
        ], path
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
                if node_id == "configuration":
                    assert target.suffix in {".toml", ".yml"}, (assertion_id, target)
                    continue
                if node_id.startswith("make "):
                    assert target == ROOT / "Makefile", (assertion_id, target)
                    command = node_id.removeprefix("make ")
                    assert re.fullmatch(r"[a-z][a-z0-9-]*", command), node_id
                    assert re.search(
                        rf"^{re.escape(command)}:",
                        target.read_text(encoding="utf-8"),
                        re.MULTILINE,
                    ), (assertion_id, node_id)
                    continue
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


def _load_site_hooks(monkeypatch: pytest.MonkeyPatch, *, shallow: str = "false") -> Any:
    spec = importlib.util.spec_from_file_location("site_hooks", DOCS / "site" / "hooks.py")
    assert spec and spec.loader
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    answers = {"rev-parse": shallow, "log": "2026-08-21T13:29:03+03:00"}
    monkeypatch.setattr(hooks, "_git", lambda _repository, command, *_args: answers[command])
    return hooks


def _site_page(**meta: str) -> SimpleNamespace:
    return SimpleNamespace(
        file=SimpleNamespace(src_uri="user/CLI.md", abs_src_path=DOCS / "user" / "CLI.md"),
        meta={"title": "CLI reference", "description": "Every command.", **meta},
        update_date="2026-11-15",
        title="CLI reference",
        canonical_url="https://joplin-mcp.romancello.net/user/CLI/",
        is_page=True,
        is_section=False,
    )


def test_site_hook_rewrites_repository_links_and_publishes_root_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = _load_site_hooks(monkeypatch)
    page = _site_page()
    config = {"docs_dir": str(DOCS), "site_dir": str(tmp_path)}

    missing_analytics = {"docs_dir": str(DOCS), "extra": {"analytics": {"property": None}}}
    empty_analytics = {"docs_dir": str(DOCS), "extra": {"analytics": {"property": "  "}}}
    configured = {"docs_dir": str(DOCS), "extra": {"analytics": {"property": "G-TEST123"}}}
    assert "analytics" not in hooks.on_config(missing_analytics)["extra"]
    assert "analytics" not in hooks.on_config(empty_analytics)["extra"]
    assert hooks.on_config(configured)["extra"]["analytics"]["property"] == "G-TEST123"

    rendered = hooks.on_page_markdown(
        "[readme](../../README.md#installation) [contract](../contracts/CLI.md)",
        page=page,
        config=config,
    )
    assert (
        "[readme](https://github.com/kogeler/joplin-md-sync/blob/main/README.md#installation)"
        in rendered
    )
    assert "[contract](../contracts/CLI.md)" in rendered
    assert page.update_date == "2026-08-21T13:29:03+03:00"

    section = SimpleNamespace(is_page=False, is_section=True, title="Markdown", children=[page])
    hooks.on_nav(SimpleNamespace(items=[section]))
    hooks.on_post_build(config=config)
    llms = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    assert "<!-- pages -->" not in llms
    assert "## Markdown\n\n- [CLI reference](https://joplin-mcp.romancello.net/user/CLI/): " in llms
    key = (DOCS / "site" / "indexnow-key.txt").read_text(encoding="utf-8").strip()
    assert (tmp_path / f"{key}.txt").read_text(encoding="utf-8") == key


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        ({"description": ""}, "needs a description"),
        ({"title": 'Say "hi"'}, "must not contain quotes"),
        ({"description": "x" * 161}, "exceeds 160 characters"),
    ],
)
def test_site_hook_rejects_unusable_page_metadata(
    monkeypatch: pytest.MonkeyPatch, meta: dict[str, str], message: str
) -> None:
    hooks = _load_site_hooks(monkeypatch)
    config = {"docs_dir": str(DOCS), "site_dir": "site"}

    with pytest.raises(hooks.SiteInputError, match=message):
        hooks.on_page_markdown("text", page=_site_page(**meta), config=config)


def test_site_hook_refuses_a_shallow_history(monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = _load_site_hooks(monkeypatch, shallow="true")

    with pytest.raises(hooks.SiteInputError, match="fetch-depth: 0"):
        hooks.on_config({"docs_dir": str(DOCS), "extra": {}})
