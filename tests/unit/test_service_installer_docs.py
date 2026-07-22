"""Documentation contract for the headless service installer CLI reference."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DOC = ROOT / "docs" / "SERVICE.md"


def test_service_installer_reference_covers_every_public_option() -> None:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    assert "## Installer CLI reference" in contents
    options = (
        "--sync-target",
        "--sync-location",
        "--sync-username",
        "--sync-secret-file",
        "--s3-endpoint",
        "--s3-region",
        "--s3-force-path-style",
        "--no-s3-force-path-style",
        "--e2ee-password-file",
        "--profile-dir",
        "--api-port",
        "--sync-interval",
        "--joplin-version",
        "--joplin-prefix",
        "--joplin-md-sync-version",
        "--mcp-port",
        "--allow-remote-mcp",
        "--upgrade",
        "--no-enable-service",
        "--no-start-service",
        "--force-reconfigure",
        "--enable-linger",
        "--purge",
        "--yes",
        "--non-interactive",
        "--dry-run",
        "--verbose",
        "--help",
    )
    for option in options:
        assert f"`{option}" in contents


def test_service_installer_reference_covers_every_environment_override() -> None:
    contents = SERVICE_DOC.read_text(encoding="utf-8")
    variables = (
        "JOPLIN_SYNC_TARGET",
        "JOPLIN_SYNC_LOCATION",
        "JOPLIN_SYNC_USERNAME",
        "JOPLIN_SYNC_SECRET_FILE",
        "JOPLIN_S3_ENDPOINT",
        "JOPLIN_S3_REGION",
        "JOPLIN_E2EE_PASSWORD_FILE",
        "JOPLIN_PROFILE_DIR",
        "JOPLIN_API_PORT",
        "JOPLIN_SYNC_INTERVAL",
        "JOPLIN_VERSION",
        "JOPLIN_INSTALL_PREFIX",
        "JOPLIN_MD_SYNC_VERSION",
        "JOPLIN_MCP_PORT",
        "JOPLIN_TERMINAL_ASSET_BASE_URL",
    )
    for variable in variables:
        assert f"`{variable}`" in contents
