"""Documentation contract for the single ChatGPT Actions setup runbook."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"


def test_chatgpt_actions_setup_is_complete_and_not_split() -> None:
    setup = (DOCS / "CHATGPT_ACTIONS.md").read_text(encoding="utf-8")
    assert not (DOCS / "CHATGPT_INSTRUCTIONS.md").exists()
    for required in (
        "python3 scripts/prepare_chatgpt_action.py",
        "asks for exactly two values",
        "chatgpt-action.openapi.json",
        "Fill in the Configure screen",
        "Joplin Notes",
        "Conversation starters",
        "Leave **Knowledge** empty",
        "No Recommended Model",
        "Web Search",
        "Image Generation",
        "Code Interpreter & Data Analysis",
        "no enabled Capability",
        "Configure the Action and its token",
        "API key",
        "Bearer",
        "Do not add the word `Bearer`",
        "Version 1.5.0 generates 27 Actions",
        "Authorization: Bearer <token>",
        "Test in Preview",
        "success: true",
        "Move only that acceptance note to Joplin trash",
    ):
        assert required in setup
    assert "cloudflare" not in setup.lower()
    assert "curl " not in setup
    assert "gpt-actions export-openapi" not in setup

    markdown = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [REPO / "README.md", REPO / "AGENTS.md", *DOCS.glob("*.md")]
    )
    assert "CHATGPT_INSTRUCTIONS.md" not in markdown


def test_service_guide_delegates_actions_setup_without_duplicate_commands() -> None:
    service = (DOCS / "SERVICE.md").read_text(encoding="utf-8")
    assert "ChatGPT Actions end-to-end setup" in service
    assert "setup-probe-not-a-tool" not in service
    assert "gpt-actions export-openapi" not in service
