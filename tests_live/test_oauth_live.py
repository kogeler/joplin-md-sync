"""All OAuth MCP tools against the real ephemeral Joplin Desktop Data API."""

from __future__ import annotations

from tests.oauth.e2e.test_oauth_flow import RunningApplication
from tests.oauth.e2e.test_oauth_tools import exercise_all_tools
from tests_live.ephemeral_joplin import running_joplin


def test_oauth_login_all_tools_refresh_and_restart_with_real_joplin(tmp_path):
    runtime = running_joplin()
    application = RunningApplication(
        tmp_path, backend=runtime, joplin_token=runtime.token_file.read_text().strip()
    )
    try:
        application.start()
        client_id, tokens = application.login()
        assert exercise_all_tools(application, tokens["access_token"])
        renewed = application.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert renewed.status_code == 200
        application.stop()
        application.start()
        assert application.rpc(renewed.json()["access_token"], "tools/list")["tools"]
    finally:
        application.close()
    application.assert_private_data_not_logged()
