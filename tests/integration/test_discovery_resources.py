"""Port discovery, doctor, and resource download round-trips."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import api as api_mod
from joplin_md_sync.errors import ApiError
from tests.fake_joplin_server import FakeJoplinServer
from tests.helpers import TOKEN, WorkspaceTestCase, run_cli


class DiscoveryTest(WorkspaceTestCase):
    def test_single_service_discovered(self):
        ports = range(self.server.port, self.server.port + 3)
        with mock.patch.object(api_mod, "DISCOVERY_PORTS", ports):
            self.assertEqual(api_mod.discover_base_url(), self.server.base_url)

    def test_no_service_is_unambiguous_error(self):
        with mock.patch.object(api_mod, "DISCOVERY_PORTS", range(1, 2)):
            with self.assertRaises(ApiError) as ctx:
                api_mod.discover_base_url()
        self.assertIn("no Joplin Clipper service", str(ctx.exception))

    def test_multiple_services_is_unambiguous_error(self):
        second = FakeJoplinServer(token=TOKEN).start()
        self.addCleanup(second.stop)
        lo, hi = sorted((self.server.port, second.port))
        with mock.patch.object(api_mod, "DISCOVERY_PORTS", [lo, hi]):
            with self.assertRaises(ApiError) as ctx:
                api_mod.discover_base_url()
        self.assertIn("multiple", str(ctx.exception))

    def test_non_joplin_service_rejected(self):
        """A server that answers /ping with the wrong body is not accepted."""
        import http.server
        import threading

        class Dummy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"NotJoplin")

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Dummy)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        with mock.patch.object(api_mod, "DISCOVERY_PORTS", [httpd.server_address[1]]):
            with self.assertRaises(ApiError):
                api_mod.discover_base_url()


class DoctorTest(WorkspaceTestCase):
    def test_healthy_workspace(self):
        self.init_and_pull()
        result = self.cli("doctor", "--root", str(self.root), "--json", expect=0)
        self.assertTrue(result.json["healthy"])
        checks = {c["check"]: c["ok"] for c in result.json["checks"]}
        for name in ("workspace", "state_database", "joplin_ping", "joplin_auth"):
            self.assertTrue(checks[name], name)

    def test_doctor_offline_skips_network(self):
        self.init_and_pull()
        self.server.stop()
        result = run_cli(
            "doctor", "--root", str(self.root), "--offline", "--json",
            env={"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": "http://127.0.0.1:1"},
        )
        self.assertEqual(result.exit_code, 0, result.stdout)

    def test_doctor_reports_auth_failure(self):
        self.init_and_pull()
        result = run_cli(
            "doctor", "--root", str(self.root), "--json",
            env={"JOPLIN_TOKEN": "bad-token", "JOPLIN_BASE_URL": self.server.base_url},
        )
        self.assertEqual(result.exit_code, 4, result.stdout)


class ResourcesTest(WorkspaceTestCase):
    seed_remote = False

    def seed_with_resource(self):
        folder = self.server.store.add_folder("Media")
        rid = self.server.store.add_resource(
            b"\x89PNG fake image bytes", mime="image/png", filename="diagram.png"
        )
        nid = self.server.store.add_note(
            "With image", f"before\n\n![diagram](:/{rid})\n\nafter\n", folder
        )
        return rid, nid

    def test_resource_links_survive_round_trip(self):
        rid, nid = self.seed_with_resource()
        self.init_and_pull()
        note = self.find_note_file("With image")
        text = note.read_text(encoding="utf-8")
        self.assertIn(f"(:/{rid})", text)
        # Edit, push, and confirm the link is still intact remotely.
        note.write_text(text + "\nlocal edit\n", encoding="utf-8")
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertIn(f":/{rid}", self.server.store.notes[nid]["body"])

    def test_resources_pull_downloads_files(self):
        rid, _ = self.seed_with_resource()
        self.init_and_pull()
        result = self.cli("resources", "pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(len(result.json["downloaded"]), 1)
        target = self.root / ".joplin-sync" / "resources" / f"{rid}.png"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), b"\x89PNG fake image bytes")
        # Second pull skips existing files.
        result = self.cli("resources", "pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["already_present"], [rid])
        # Markdown was not rewritten.
        self.assertIn(
            f"(:/{rid})", self.find_note_file("With image").read_text(encoding="utf-8")
        )

    def test_note_links_not_treated_as_missing_resources(self):
        """Joplin note-to-note links use the same :/id syntax as resources."""
        folder = self.server.store.add_folder("Linked")
        target = self.server.store.add_note("Target", "target body\n", folder)
        self.server.store.add_note("Source", f"see [target](:/{target})\n", folder)
        self.init_and_pull()
        result = self.cli("resources", "pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["missing"], [])
        self.assertEqual(result.json["note_links_skipped"], [target])
        self.assertEqual(result.json["downloaded"], [])

    def test_note_validate_command(self):
        self.seed_with_resource()
        self.init_and_pull()
        note = self.find_note_file("With image")
        result = self.cli("note", "validate", str(note), "--json", expect=0)
        self.assertTrue(result.json["valid"])
        bad = note.parent / "bad.md"
        bad.write_text("<!-- joplin-md-sync: nope -->\n\nx\n", encoding="utf-8")
        result = self.cli("note", "validate", str(bad), "--json", expect=3)
        self.assertFalse(result.json["valid"])


if __name__ == "__main__":
    import unittest

    unittest.main()
