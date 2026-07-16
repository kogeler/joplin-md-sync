"""Agent output contract: deterministic JSON, exit codes, security properties."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.helpers import TOKEN, WorkspaceTestCase, run_cli


class JsonContractTest(WorkspaceTestCase):
    def test_json_envelope_fields(self):
        self.init_and_pull()
        result = self.cli("status", "--root", str(self.root), "--json", expect=0)
        for key in ("schema_version", "command", "success", "exit_code", "code",
                    "tool_version", "workspace"):
            self.assertIn(key, result.json, key)
        self.assertEqual(result.json["schema_version"], 1)

    def test_json_is_deterministic(self):
        self.init_and_pull()
        a = self.cli("diff", "--root", str(self.root), "--json", expect=0).stdout
        b = self.cli("diff", "--root", str(self.root), "--json", expect=0).stdout
        self.assertEqual(a, b)

    def test_json_has_no_ansi_codes(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        note.write_text(
            note.read_text(encoding="utf-8") + "\nchange\n", encoding="utf-8"
        )
        out = self.cli("diff", "--root", str(self.root), "--json", "--unified", expect=0).stdout
        self.assertNotRegex(out, r"\x1b\[")
        json.loads(out)  # valid JSON

    def test_diff_never_mutates(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        note.write_text(note.read_text(encoding="utf-8") + "\nchange\n", encoding="utf-8")
        remote_before = json.dumps(self.server.store.notes, sort_keys=True)
        tree_before = self.tree_digest()
        self.cli("diff", "--root", str(self.root), "--json", "--three-way", "--unified", expect=0)
        self.assertEqual(json.dumps(self.server.store.notes, sort_keys=True), remote_before)
        self.assertEqual(self.tree_digest(), tree_before)

    def test_diff_exit_codes(self):
        self.init_and_pull()
        # Clean: 0 with or without --exit-code.
        self.cli("diff", "--root", str(self.root), "--json", expect=0)
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)
        # Changed: 0 by default (git-diff semantics), 1 with --exit-code.
        note = self.find_note_file("Kubernetes")
        note.write_text(note.read_text(encoding="utf-8") + "\nx\n", encoding="utf-8")
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["code"], "DIFF_FOUND")
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=1)

    def test_diff_unified_labels(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        note.write_text(note.read_text(encoding="utf-8") + "\nlocal line\n", encoding="utf-8")
        out = self.cli(
            "diff", "--root", str(self.root), "--three-way", "--unified", expect=0
        ).stdout
        self.assertIn(f"base/{self.note_k8s}", out)
        self.assertRegex(out, r"local/Work/Kubernetes--\w{8}\.md")

    def test_diff_note_filter(self):
        self.init_and_pull()
        for fragment in ("Kubernetes", "Plans"):
            note = self.find_note_file(fragment)
            note.write_text(note.read_text(encoding="utf-8") + "\nx\n", encoding="utf-8")
        result = self.cli(
            "diff", "--root", str(self.root), "--json", "--note", self.note_k8s, expect=0
        )
        changed = [i for i in result.json["items"] if i["status"] != "UNCHANGED"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["note_id"], self.note_k8s)

    def test_diff_offline_marks_remote_unknown(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        note.write_text(note.read_text(encoding="utf-8") + "\nx\n", encoding="utf-8")
        self.server.stop()  # no network available at all
        result = run_cli(
            "diff", "--root", str(self.root), "--json", "--offline",
            env={"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": "http://127.0.0.1:1"},
        )
        self.assertEqual(result.exit_code, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["offline"])
        changed = [i for i in payload["items"] if i["status"] == "LOCAL_MODIFIED"]
        self.assertEqual(changed[0]["remote_state"], "unknown")


class ExitCodeTest(WorkspaceTestCase):
    def test_invalid_token_is_auth_failure(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json",
            env={"JOPLIN_TOKEN": "wrong-token", "JOPLIN_BASE_URL": self.server.base_url},
        )
        self.assertEqual(result.exit_code, 4, result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "API_AUTH_FAILED")

    def test_missing_token_reported(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json",
            env={"JOPLIN_BASE_URL": self.server.base_url},
        )
        self.assertEqual(result.exit_code, 4)

    def test_api_unreachable(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json",
            env={"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": "http://127.0.0.1:1"},
        )
        self.assertEqual(result.exit_code, 4)

    def test_uninitialized_workspace(self):
        result = self.cli("status", "--root", str(self.root), "--json", expect=3)
        self.assertEqual(result.json["code"], "INVALID_WORKSPACE")

    def test_malformed_managed_file_blocks_its_push(self):
        self.init_and_pull()
        bad = self.root / "Work" / "broken.md"
        bad.write_text("<!-- joplin-md-sync: {broken json} -->\n\nbody\n", encoding="utf-8")
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        invalid = [i for i in result.json["items"] if i["status"] == "INVALID_LOCAL_FILE"]
        self.assertEqual(len(invalid), 1)
        # Push proceeds for other notes but never touches the invalid file.
        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        titles = {n["title"] for n in self.server.store.notes.values()}
        self.assertNotIn("broken", titles)

    def test_non_loopback_requires_flag(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json",
            env={"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": "http://192.0.2.10:41184"},
        )
        self.assertEqual(result.exit_code, 7, result.stdout)
        self.assertEqual(json.loads(result.stdout)["code"], "UNSAFE_OPERATION_BLOCKED")


class TokenSafetyTest(WorkspaceTestCase):
    def _assert_no_token(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn(TOKEN, text)

    def test_token_never_in_output_on_auth_error(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json", "--verbose",
            env={"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": "http://127.0.0.1:1"},
        )
        self._assert_no_token(result.stdout, result.stderr)

    def test_token_never_in_log_file(self):
        self.init_and_pull()
        log_file = self.root.parent / "debug.log"
        self.cli(
            "doctor", "--root", str(self.root), "--json", "--verbose",
            "--log-file", str(log_file), expect=0,
        )
        if log_file.exists():
            self._assert_no_token(log_file.read_text(encoding="utf-8"))

    def test_token_not_stored_in_workspace(self):
        self.init_and_pull()
        for path in self.root.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".md", ".gitignore", ""):
                try:
                    self._assert_no_token(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, OSError):
                    continue

    def test_token_file_option(self):
        token_file = self.root.parent / "token.txt"
        token_file.write_text(TOKEN + "\n", encoding="utf-8")
        result = run_cli(
            "init", "--root", str(self.root), env={"JOPLIN_BASE_URL": self.server.base_url}
        )
        self.assertEqual(result.exit_code, 0)
        result = run_cli(
            "pull", "--root", str(self.root), "--json", "--token-file", str(token_file),
            env={"JOPLIN_BASE_URL": self.server.base_url},
        )
        self.assertEqual(result.exit_code, 0, result.stdout)


class SecurityScanTest(WorkspaceTestCase):
    def test_symlink_not_followed(self):
        if sys.platform == "win32":
            self.skipTest("symlink semantics differ on Windows")
        self.init_and_pull()
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("secret\n", encoding="utf-8")
        (self.root / "Work" / "link").symlink_to(outside)
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        invalid = [
            i for i in result.json["items"]
            if i["status"] == "INVALID_LOCAL_FILE" and "symlink" in (i.get("detail") or "")
        ]
        self.assertEqual(len(invalid), 1)
        # The symlinked content is never pushed.
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        titles = {n["title"] for n in self.server.store.notes.values()}
        self.assertNotIn("secret", titles)


class LocalFirstModeTest(WorkspaceTestCase):
    def test_local_first_requires_dry_run_before_push(self):
        self.root.mkdir(parents=True)
        docs = self.root / "Docs"
        docs.mkdir()
        (docs / "note one.md").write_text("first note body\n", encoding="utf-8")
        self.cli("init", "--root", str(self.root), "--mode", "local-first", "--json", expect=0)

        result = self.cli("push", "--root", str(self.root), "--json", expect=7)
        self.assertEqual(result.json["code"], "UNSAFE_OPERATION_BLOCKED")

        result = self.cli("push", "--root", str(self.root), "--dry-run", "--json", expect=1)
        kinds = [op["kind"] for op in result.json["planned_operations"]]
        self.assertIn("push_create_folder", kinds)
        self.assertIn("push_create_remote", kinds)

        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        titles = {n["title"] for n in self.server.store.notes.values()}
        self.assertIn("note one", titles)
        # Notes are never matched to unrelated remote notes by title.
        self.assertEqual(
            len([n for n in self.server.store.notes.values() if n["title"] == "note one"]), 1
        )


class UpdateCheckCliTest(WorkspaceTestCase):
    seed_remote = False

    def test_offline_skip(self):
        result = self.cli("update-check", "--offline", "--json", expect=0)
        self.assertEqual(result.json["code"], "UPDATE_CHECK_SKIPPED")

    def test_outdated_exit_code(self):
        from unittest import mock

        from joplin_md_sync import update_check as uc

        with mock.patch.object(uc, "_fetch_json", return_value={"tag_name": "v99.0.0"}):
            result = self.cli("update-check", "--json", expect=8)
        self.assertEqual(result.json["code"], "VERSION_OUTDATED")
        self.assertIn("pip install", result.json["update_command"])

    def test_unreachable_github_is_operational_error(self):
        from unittest import mock

        from joplin_md_sync import update_check as uc

        with mock.patch.object(uc, "_fetch_json", side_effect=OSError("no net")):
            result = self.cli("update-check", "--json", expect=4)
        self.assertEqual(result.json["code"], "UPDATE_CHECK_FAILED")


if __name__ == "__main__":
    import unittest

    unittest.main()
