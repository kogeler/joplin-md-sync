"""Conflict detection, bundles, resolution, staleness protection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.helpers import WorkspaceTestCase


class ConflictFlowTest(WorkspaceTestCase):
    def _make_divergent(self) -> Path:
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\n# Cluster\n\nlocal version\n", encoding="utf-8")
        self.server.store.notes[self.note_k8s]["body"] = "# Cluster\n\nremote version\n"
        self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
        return note

    def test_divergent_edit_creates_conflict_without_overwrites(self):
        note = self._make_divergent()
        local_before = note.read_text(encoding="utf-8")
        remote_before = self.server.store.notes[self.note_k8s]["body"]

        result = self.cli("sync", "--root", str(self.root), "--json", expect=2)
        self.assertEqual(result.json["code"], "CONFLICTS_PRESENT")
        self.assertEqual(result.json["open_conflicts"], 1)
        # Neither side was overwritten.
        self.assertEqual(note.read_text(encoding="utf-8"), local_before)
        self.assertEqual(self.server.store.notes[self.note_k8s]["body"], remote_before)

        # Bundle exists with all three sides.
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2)
        conflicts = listing.json["conflicts"]
        self.assertEqual(len(conflicts), 1)
        cid = conflicts[0]["conflict_id"]
        shown = self.cli("conflicts", "show", cid, "--root", str(self.root), "--json", expect=0)
        self.assertIn("local version", shown.json["sides"]["local"])
        self.assertIn("remote version", shown.json["sides"]["remote"])
        self.assertIn("line one", shown.json["sides"]["base"])
        self.assertTrue(shown.json["resolution_commands"])

        # A second sync must not duplicate the conflict.
        result = self.cli("sync", "--root", str(self.root), "--json", expect=2)
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2)
        self.assertEqual(len(listing.json["conflicts"]), 1)

    def _conflict_id(self) -> str:
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2)
        return listing.json["conflicts"][0]["conflict_id"]

    def test_resolve_take_local(self):
        self._make_divergent()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self._conflict_id()
        self.cli(
            "conflicts", "resolve", cid, "--take-local", "--root", str(self.root), "--json",
            expect=0,
        )
        self.assertEqual(self.server.store.notes[self.note_k8s]["body"], "# Cluster\n\nlocal version\n")
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)
        self.assertFalse((self.root / ".joplin-sync" / "conflicts" / cid).exists())

    def test_resolve_take_remote(self):
        note = self._make_divergent()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self._conflict_id()
        self.cli(
            "conflicts", "resolve", cid, "--take-remote", "--root", str(self.root), "--json",
            expect=0,
        )
        self.assertIn("remote version", note.read_text(encoding="utf-8"))
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_resolve_merged_file(self):
        self._make_divergent()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self._conflict_id()
        merged = self.root / ".." / "merged.md"
        merged.write_text(
            f'<!-- joplin-md-sync: {{"id":"{self.note_k8s}","schema":1,"tags":["homelab"],'
            f'"title":"Kubernetes"}} -->\n\n# Cluster\n\nmerged of both\n',
            encoding="utf-8",
        )
        self.cli(
            "conflicts", "resolve", cid, "--merged-file", str(merged),
            "--root", str(self.root), "--json", expect=0,
        )
        self.assertEqual(self.server.store.notes[self.note_k8s]["body"], "# Cluster\n\nmerged of both\n")
        self.assertIn("merged of both", self.find_note_file("Kubernetes").read_text(encoding="utf-8"))
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_stale_resolution_refused_after_remote_change(self):
        self._make_divergent()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self._conflict_id()
        # Remote changes again after the bundle was created.
        self.server.store.notes[self.note_k8s]["body"] = "# Cluster\n\neven newer remote\n"
        self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
        result = self.cli(
            "conflicts", "resolve", cid, "--take-local", "--root", str(self.root), "--json",
            expect=5,
        )
        self.assertEqual(result.json["code"], "CONCURRENT_MODIFICATION")
        # Nothing was applied.
        self.assertEqual(
            self.server.store.notes[self.note_k8s]["body"], "# Cluster\n\neven newer remote\n"
        )

    def test_discard_conflict(self):
        self._make_divergent()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self._conflict_id()
        self.cli("conflicts", "discard", cid, "--root", str(self.root), "--json", expect=0)
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=0)
        self.assertEqual(listing.json["conflicts"], [])
        # Sides still diverge, so the next sync re-detects the conflict.
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        self.assertEqual(len(self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2).json["conflicts"]), 1)


class DeleteConflictTest(WorkspaceTestCase):
    def test_remote_deleted_local_edited(self):
        self.init_and_pull()
        note = self.find_note_file("Plans")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nlocal edit of deleted note\n", encoding="utf-8")
        self.server.store.notes[self.note_plans]["deleted_time"] = self.server.store.tick()

        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        self.assertTrue(note.is_file())  # never silently deleted
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2)
        self.assertEqual(listing.json["conflicts"][0]["category"], "delete_remote_edit_local")

        # take-local recreates the note in Joplin under the same id.
        cid = listing.json["conflicts"][0]["conflict_id"]
        self.cli(
            "conflicts", "resolve", cid, "--take-local", "--root", str(self.root), "--json",
            expect=0,
        )
        remote = self.server.store.notes[self.note_plans]
        self.assertEqual(remote["deleted_time"], 0)
        self.assertIn("local edit of deleted note", remote["body"])

    def test_remote_trashed_take_local_restores_via_put(self):
        """Real-Joplin fidelity: restore = PUT deleted_time=0, never POST.

        The fake server now fails POST /notes with an existing id exactly
        like real Joplin (UNIQUE constraint), so this test proves the
        restore path avoids POST entirely.
        """
        self.init_and_pull()
        note = self.find_note_file("Plans")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nedited after remote trash\n", encoding="utf-8")
        self.server.store.notes[self.note_plans]["deleted_time"] = self.server.store.tick()
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2).json[
            "conflicts"
        ][0]["conflict_id"]
        self.cli(
            "conflicts", "resolve", cid, "--take-local", "--root", str(self.root), "--json",
            expect=0,
        )
        remote = self.server.store.notes[self.note_plans]
        self.assertEqual(remote["deleted_time"], 0)
        self.assertIn("edited after remote trash", remote["body"])

    def test_remote_permanently_deleted_take_local_recreates(self):
        """When the note is fully gone (not trash), take-local recreates it."""
        self.init_and_pull()
        note = self.find_note_file("Plans")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nedited after permanent delete\n", encoding="utf-8")
        del self.server.store.notes[self.note_plans]  # permanent deletion
        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        cid = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2).json[
            "conflicts"
        ][0]["conflict_id"]
        self.cli(
            "conflicts", "resolve", cid, "--take-local", "--root", str(self.root), "--json",
            expect=0,
        )
        remote = self.server.store.notes[self.note_plans]
        self.assertIn("edited after permanent delete", remote["body"])
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_local_deleted_remote_edited(self):
        self.init_and_pull()
        self.find_note_file("Plans").unlink()
        self.server.store.notes[self.note_plans]["body"] = "remote kept editing\n"
        self.server.store.notes[self.note_plans]["updated_time"] = self.server.store.tick()

        self.cli("sync", "--root", str(self.root), "--json", expect=2)
        listing = self.cli("conflicts", "list", "--root", str(self.root), "--json", expect=2)
        self.assertEqual(listing.json["conflicts"][0]["category"], "delete_local_edit_remote")

        # take-remote restores the file locally.
        cid = listing.json["conflicts"][0]["conflict_id"]
        self.cli(
            "conflicts", "resolve", cid, "--take-remote", "--root", str(self.root), "--json",
            expect=0,
        )
        self.assertIn(
            "remote kept editing", self.find_note_file("Plans").read_text(encoding="utf-8")
        )
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)


class JoplinConflictNoteTest(WorkspaceTestCase):
    def test_joplin_conflict_notes_surfaced_and_skipped(self):
        self.server.store.add_note(
            "Conflicted elsewhere", "conflict body\n", self.folder_work, is_conflict=1
        )
        self.init_and_pull()
        # The conflict note is not pulled as a file.
        conflict_files = [
            p for p in self.root.rglob("*.md")
            if "Conflicted" in p.name and ".joplin-sync" not in p.parts
        ]
        self.assertEqual(conflict_files, [])
        result = self.cli("diff", "--root", str(self.root), "--json", expect=0)
        statuses = {i["status"] for i in result.json["items"]}
        self.assertIn("JOPLIN_CONFLICT_NOTE", statuses)


if __name__ == "__main__":
    import unittest

    unittest.main()
