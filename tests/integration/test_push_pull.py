"""Push and pull flows: edits, new notes, tags, moves, deletions."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.helpers import WorkspaceTestCase


class PushTest(WorkspaceTestCase):
    def test_local_edit_then_push(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        text = note.read_text(encoding="utf-8")
        note.write_text(text + "\nlocal addition\n", encoding="utf-8")

        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        statuses = {i["status"] for i in result.json["items"]}
        self.assertIn("LOCAL_MODIFIED", statuses)

        result = self.cli("push", "--root", str(self.root), "--dry-run", "--json", expect=1)
        self.assertEqual(len(result.json["planned_operations"]), 1)

        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 1)
        self.assertIn("local addition", self.server.store.notes[self.note_k8s]["body"])

        result = self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_dry_run_mutates_nothing(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        note.write_text(note.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
        before_remote = json.dumps(self.server.store.notes, sort_keys=True)
        before_tree = self.tree_digest()
        self.cli("push", "--root", str(self.root), "--dry-run", "--json", expect=1)
        self.assertEqual(json.dumps(self.server.store.notes, sort_keys=True), before_remote)
        self.assertEqual(self.tree_digest(), before_tree)

    def test_new_local_note_with_header(self):
        self.init_and_pull()
        new = self.root / "Work" / "fresh.md"
        new.write_text(
            '<!-- joplin-md-sync: {"schema":1,"tags":["new-tag"],"title":"Fresh note"} -->\n\nfresh body\n',
            encoding="utf-8",
        )
        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 1)
        created = [n for n in self.server.store.notes.values() if n["title"] == "Fresh note"]
        self.assertEqual(len(created), 1)
        self.assertEqual(self.server.store.note_tag_titles(created[0]["id"]), ["new-tag"])
        # The returned Joplin id was written into the local header atomically
        # and the file was renamed to its canonical name.
        self.assertFalse(new.exists())
        renamed = self.find_note_file("Fresh note")
        self.assertIn(f'"id":"{created[0]["id"]}"', renamed.read_text(encoding="utf-8"))

    def test_new_headerless_note_adopted_on_push(self):
        self.init_and_pull()
        new = self.root / "Work" / "scratch pad.md"
        new.write_text("plain markdown body\n", encoding="utf-8")
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        created = [n for n in self.server.store.notes.values() if n["title"] == "scratch pad"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["body"], "plain markdown body\n")

    def test_tag_add_and_remove(self):
        self.init_and_pull()
        self.cli(
            "note", "set-tags", str(self.find_note_file("Kubernetes")),
            "kubernetes", "ops", expect=0,
        )
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(
            self.server.store.note_tag_titles(self.note_k8s), ["kubernetes", "ops"]
        )
        # Removing all tags must also propagate ("homelab" was removed above).
        self.cli("note", "set-tags", str(self.find_note_file("Kubernetes")), expect=0)
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(self.server.store.note_tag_titles(self.note_k8s), [])

    def test_title_change_renames_file_and_updates_remote(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        self.cli("note", "set-title", str(note), "K8s cluster notes", expect=0)
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(self.server.store.notes[self.note_k8s]["title"], "K8s cluster notes")
        renamed = self.find_note_file("K8s cluster notes")
        self.assertTrue(renamed.name.startswith("K8s cluster notes--"))

    def test_note_moved_between_notebooks(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        target = self.root / "Personal" / note.name
        note.rename(target)
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        statuses = {i["status"] for i in result.json["items"]}
        self.assertIn("MOVED_LOCAL", statuses)
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(
            self.server.store.notes[self.note_k8s]["parent_id"], self.folder_personal
        )

    def test_new_local_notebook_pushed(self):
        self.init_and_pull()
        new_dir = self.root / "Brand New"
        new_dir.mkdir()
        (new_dir / "inside.md").write_text(
            '<!-- joplin-md-sync: {"schema":1,"tags":[],"title":"Inside"} -->\n\nbody\n',
            encoding="utf-8",
        )
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        folders = [f for f in self.server.store.folders.values() if f["title"] == "Brand New"]
        self.assertEqual(len(folders), 1)
        notes = [n for n in self.server.store.notes.values() if n["title"] == "Inside"]
        self.assertEqual(notes[0]["parent_id"], folders[0]["id"])
        self.assertTrue((new_dir / ".joplin-folder.json").is_file())

    def test_local_delete_reported_not_propagated(self):
        self.init_and_pull()
        self.find_note_file("Plans").unlink()
        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 0)
        self.assertFalse(self.server.store.notes[self.note_plans].get("deleted_time"))
        self.assertEqual(result.json["pending_deletions_not_propagated"], 1)

    def test_local_delete_propagated_to_trash(self):
        self.init_and_pull()
        self.find_note_file("Plans").unlink()
        result = self.cli(
            "push", "--root", str(self.root), "--propagate-deletes", "--json", expect=0
        )
        self.assertEqual(result.json["execution"]["applied"], 1)
        note = self.server.store.notes[self.note_plans]
        self.assertGreater(note["deleted_time"], 0)  # trash, not permanent


class PullChangesTest(WorkspaceTestCase):
    def test_remote_edit_then_pull(self):
        self.init_and_pull()
        self.server.store.notes[self.note_k8s]["body"] = "# Cluster\n\nremote update\n"
        self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
        result = self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 1)
        self.assertIn("remote update", self.find_note_file("Kubernetes").read_text(encoding="utf-8"))

    def test_remote_new_note_pulled(self):
        self.init_and_pull()
        self.server.store.add_note("Later note", "created later\n", self.folder_work)
        self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertTrue(self.find_note_file("Later note").is_file())

    def test_remote_tag_change_detected_without_updated_time_bump(self):
        self.init_and_pull()
        tag = self.server.store.add_tag("added-remotely")
        self.server.store.tag_note(tag, self.note_plans)  # does NOT bump updated_time
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        modified = [i for i in result.json["items"] if i["status"] == "REMOTE_MODIFIED"]
        self.assertEqual(len(modified), 1)
        self.assertEqual(modified[0]["remote_changed"], ["tags"])
        self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertIn(
            '"tags":["added-remotely"]',
            self.find_note_file("Plans").read_text(encoding="utf-8"),
        )

    def test_remote_title_change_renames_local_file(self):
        self.init_and_pull()
        self.server.store.notes[self.note_plans]["title"] = "Renamed plans"
        self.server.store.notes[self.note_plans]["updated_time"] = self.server.store.tick()
        self.cli("pull", "--root", str(self.root), "--json", expect=0)
        renamed = self.find_note_file("Renamed plans")
        self.assertIn("Renamed plans", renamed.name)

    def test_remote_delete_reported_then_quarantined(self):
        self.init_and_pull()
        self.server.store.notes[self.note_plans]["deleted_time"] = self.server.store.tick()
        result = self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["execution"]["applied"], 0)
        self.assertTrue(self.find_note_file("Plans").is_file())  # kept

        result = self.cli(
            "pull", "--root", str(self.root), "--propagate-deletes", "--json", expect=0
        )
        self.assertEqual(result.json["execution"]["applied"], 1)
        remaining = [
            p for p in self.root.rglob("Plans--*.md") if ".joplin-sync" not in p.parts
        ]
        self.assertEqual(remaining, [])
        quarantined = list((self.root / ".joplin-sync" / "quarantine").rglob("*.md"))
        self.assertEqual(len(quarantined), 1)

    def test_remote_notebook_rename_moves_directory(self):
        self.init_and_pull()
        self.server.store.folders[self.folder_work]["title"] = "Work Renamed"
        self.cli("pull", "--root", str(self.root), "--json", expect=0)
        self.assertTrue((self.root / "Work Renamed").is_dir())
        self.assertFalse((self.root / "Work").exists())
        # Notes inside moved with the directory and stay clean.
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_identical_concurrent_edits_rebase_without_conflict(self):
        self.init_and_pull()
        new_body = "# Cluster\n\nsame change\n"
        note = self.find_note_file("Kubernetes")
        text = note.read_text(encoding="utf-8")
        header = text.split("\n", 1)[0]
        note.write_text(header + "\n\n" + new_body, encoding="utf-8")
        self.server.store.notes[self.note_k8s]["body"] = new_body
        self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
        result = self.cli("sync", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(result.json["open_conflicts"], 0)
        self.assertEqual(result.json["summary"]["conflicts"], 0)
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)


if __name__ == "__main__":
    import unittest

    unittest.main()
