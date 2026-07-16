"""Initial synchronization: init, first pull, filenames, pagination, unicode."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.helpers import WorkspaceTestCase


class InitTest(WorkspaceTestCase):
    def test_remote_first_init_and_pull(self):
        result = self.cli("init", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["code"], "OK")
        self.assertTrue((self.root / ".joplin-sync" / "state.sqlite3").is_file())
        self.assertIn(".joplin-sync/", (self.root / ".gitignore").read_text())

        result = self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertTrue(result.json["success"])
        self.assertTrue((self.root / "Work" / ".joplin-folder.json").is_file())
        note = self.find_note_file("Kubernetes")
        text = note.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- joplin-md-sync: "))
        self.assertIn('"tags":["homelab"]', text)
        self.assertIn("# Cluster", text)

        # A second pull and a diff report a clean workspace.
        result = self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 0)
        result = self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)
        self.assertEqual(result.json["summary"]["conflicts"], 0)

    def test_double_init_rejected(self):
        self.cli("init", "--root", str(self.root), expect=0)
        result = self.cli("init", "--root", str(self.root), "--json", expect=3)
        self.assertIn("already initialized", result.json["error"])

    def test_remote_first_refuses_existing_markdown(self):
        self.root.mkdir(parents=True)
        (self.root / "existing.md").write_text("hello", encoding="utf-8")
        result = self.cli("init", "--root", str(self.root), "--json", expect=7)
        self.assertEqual(result.json["code"], "UNSAFE_OPERATION_BLOCKED")
        self.assertIn("existing.md", json.dumps(result.json))
        # local-first accepts the same directory.
        result = self.cli("init", "--root", str(self.root), "--mode", "local-first", expect=0)


class PullShapeTest(WorkspaceTestCase):
    seed_remote = False

    def test_pagination_over_100_notes(self):
        folder = self.server.store.add_folder("Bulk")
        for i in range(250):
            self.server.store.add_note(f"Note {i:03d}", f"body {i}\n", folder)
        self.init_and_pull()
        files = list((self.root / "Bulk").glob("*.md"))
        self.assertEqual(len(files), 250)

    def test_nested_and_empty_notebooks(self):
        parent = self.server.store.add_folder("Parent")
        child = self.server.store.add_folder("Child", parent_id=parent)
        self.server.store.add_folder("Empty")
        self.server.store.add_note("Deep", "text\n", child)
        self.init_and_pull()
        self.assertTrue((self.root / "Parent" / "Child" / ".joplin-folder.json").is_file())
        self.assertTrue((self.root / "Empty" / ".joplin-folder.json").is_file())
        self.assertEqual(len(list((self.root / "Parent" / "Child").glob("*.md"))), 1)

    def test_cyrillic_titles_and_bodies(self):
        folder = self.server.store.add_folder("Заметки")
        self.server.store.add_note("Кластер Кубернетес", "тело заметки: ёжик\n", folder)
        self.init_and_pull()
        note = self.find_note_file("Кластер")
        self.assertIn("ёжик", note.read_text(encoding="utf-8"))
        self.assertTrue((self.root / "Заметки").is_dir())

    def test_windows_reserved_filename(self):
        folder = self.server.store.add_folder("F")
        self.server.store.add_note("CON", "device?\n", folder)
        self.init_and_pull()
        files = list((self.root / "F").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertFalse(files[0].name.upper().startswith("CON--"))

    def test_case_insensitive_note_collision(self):
        folder = self.server.store.add_folder("F")
        self.server.store.add_note("Same Title", "a\n", folder)
        self.server.store.add_note("same title", "b\n", folder)
        self.init_and_pull()
        files = list((self.root / "F").glob("*.md"))
        self.assertEqual(len(files), 2)
        self.assertNotEqual(files[0].name.casefold(), files[1].name.casefold())

    def test_crlf_body_canonicalized(self):
        folder = self.server.store.add_folder("F")
        nid = self.server.store.add_note("CRLF", "line1\r\nline2\r\n", folder)
        self.init_and_pull()
        text = self.find_note_file("CRLF").read_text(encoding="utf-8")
        self.assertNotIn("\r", text)
        # And the pulled state must be immediately clean.
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)
        _ = nid


if __name__ == "__main__":
    import unittest

    unittest.main()
