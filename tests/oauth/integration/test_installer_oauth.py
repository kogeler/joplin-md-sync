"""Installer opt-in and candidate checks against the real read-only CLI."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from joplin_md_sync import __version__
from tests.helpers import TOKEN
from tests.oauth.e2e.test_oauth_flow import RunningApplication
from tests.oauth.helpers import OAuthArguments, protected_file

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "joplin_terminal_service"))

if sys.platform != "linux":
    pytest.skip("The systemd installer is Linux-only", allow_module_level=True)

from joplin_terminal_common import SecretRedactor, ToolError  # noqa: E402
from joplin_terminal_installer import cli, commands, managed, models, service  # noqa: E402
from joplin_terminal_installer import lifecycle as application  # noqa: E402
from joplin_terminal_installer import oauth as oauth_setup  # noqa: E402


@pytest.fixture
def install(tmp_path):
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    args = cli.build_parser(env).parse_args(
        ["--upgrade", "--joplin-version", "3.6.2", "--joplin-md-sync-version", __version__]
    )
    paths = cli.build_paths(args, env)
    for directory in (paths.config_dir, paths.mcp_config_dir):
        directory.mkdir(mode=0o700, parents=True)
    protected_file(paths.token_file, TOKEN)
    protected_file(paths.mcp_auth_token_file, base64.urlsafe_b64encode(os.urandom(32)).decode())
    protected_file(paths.gpt_actions_token_file, base64.urlsafe_b64encode(os.urandom(32)).decode())
    oauth = OAuthArguments(tmp_path)
    oauth.values["state_dir"] = str(paths.oauth_state_path)
    args = cli.build_parser(env).parse_args(
        [
            "--upgrade",
            "--non-interactive",
            "--joplin-version",
            "3.6.2",
            "--joplin-md-sync-version",
            __version__,
            *oauth.argv(),
        ]
    )
    candidate = tmp_path / "candidate"
    candidate.write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(REPO / 'src')!r})\nfrom joplin_md_sync.cli import main\nraise SystemExit(main())\n"
    )
    candidate.chmod(0o700)
    runner = commands.CommandRunner(SecretRedactor(), env)
    return args, paths, oauth, candidate, runner, env


def test_oauth_preflight_is_readonly_and_unit_has_scoped_write_path(install):
    args, paths, oauth, candidate, runner, _ = install
    oauth_setup.prepare_oauth_service(runner, paths, args, binary=candidate, provision=False)
    assert not paths.oauth_state_path.exists()
    oauth_setup.prepare_oauth_service(runner, paths, args, binary=candidate)
    assert paths.oauth_state_path.stat().st_mode & 0o777 == 0o700
    assert paths.oauth_state_path in managed.managed_purge_paths(paths)
    template = (REPO / "scripts/joplin_terminal_service/systemd/joplin-md-sync.service").read_text()
    content = service.render_adapter_unit(
        template,
        paths=paths,
        api_port=args.api_port,
        mcp_port=args.mcp_port,
        github_oauth_args=oauth.argv(),
        oauth_state_path=paths.oauth_state_path,
    )
    assert content.count("--mcp-port") == 1
    assert "--github-oauth" in content
    assert "ProtectHome=read-only" in content
    assert 'ReadWritePaths="' + str(paths.oauth_state_path) + '"' in content
    assert "fixture-secret" not in content
    assert "TimeoutStopSec=70s" in content
    assert oauth.secret_path not in managed.managed_purge_paths(paths)
    assert oauth.signing_path not in managed.managed_purge_paths(paths)
    # Disabling removes only the unit integration; grants and config remain.
    legacy = service.render_adapter_unit(
        template, paths=paths, api_port=args.api_port, mcp_port=args.mcp_port
    )
    assert "--github-oauth" not in legacy and "ReadWritePaths=" not in legacy
    assert paths.oauth_state_path.exists() and oauth.secret_path.exists()


def test_unregistered_custom_oauth_state_is_preserved(install):
    args, paths, oauth, candidate, runner, _ = install
    custom = oauth.directory / "operator-owned-oauth"
    args.github_oauth_state_dir = str(custom)
    oauth_setup.prepare_oauth_service(runner, paths, args, binary=candidate)
    assert custom.exists()
    assert custom not in managed.managed_purge_paths(paths)
    assert not (paths.mcp_config_dir / "oauth-managed-state.json").exists()


def test_failed_oauth_candidate_check_keeps_healthy_services_and_old_binary(install):
    args, paths, _oauth, candidate, _, env = install
    old_args = cli.build_parser(env).parse_args([])
    service.install_unit(
        paths, old_args, paths.deploy_dir / "run_joplin_terminal.py", Path("/node")
    )
    service.install_adapter_unit(paths, old_args)
    paths.mcp_binary.parent.mkdir(parents=True, exist_ok=True)
    paths.mcp_binary.write_bytes(b"previous-executable")
    before = paths.adapter_unit_path.read_bytes()
    args.github_oauth_allowed_user_id = "*"
    dependencies = models.Dependencies(
        Path("/node"), Path("/npm"), Path("/systemctl"), "fixture", "fixture"
    )
    tool = application.Installer(args, env=env)
    with (
        mock.patch.object(application, "check_dependencies", return_value=dependencies),
        mock.patch.object(application, "installed_mcp_version", return_value="previous"),
        mock.patch.object(application, "download_mcp_binary", return_value=candidate),
        mock.patch.object(application, "systemctl_command") as systemctl,
        mock.patch.object(application, "backup_upgrade_files") as backup,
    ):
        with pytest.raises(ToolError, match=r"OAuth candidate.*allowed_github_user_id"):
            tool.run()
        systemctl.assert_not_called()
        backup.assert_not_called()
    assert paths.mcp_binary.read_bytes() == b"previous-executable"
    assert paths.adapter_unit_path.read_bytes() == before
    assert not paths.oauth_state_path.exists()
    assert not candidate.exists()


def test_installer_probes_real_oauth_challenge_and_discovery(tmp_path):
    application = RunningApplication(tmp_path)
    try:
        application.start()
        oauth_setup.smoke_test_oauth_service(application.port, application.proxy.origin)
        with pytest.raises(ToolError, match="challenge smoke"):
            oauth_setup.smoke_test_oauth_service(application.port, "https://attacker.invalid")
        assert application.github.github.user_requests == 0
        assert not application.backend.store.notes
    finally:
        application.close()
