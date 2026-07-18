import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.errors import WorkspaceError
from joplin_md_sync.state import MIGRATIONS, StateDB, StateStore

NID = "a" * 32


class StateDBTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.sqlite3"

    def open_store(self, create=False) -> StateStore:
        store = StateStore(StateDB(self.path).connect(create=create))
        self.addCleanup(store.close)
        return store

    def test_note_round_trip(self):
        store = self.open_store(create=True)
        store.upsert_note(
            note_id=NID, rel_path="Work/T--aaaaaaaa.md", title="T", body="b\n",
            tags=("x", "a"), parent_id="p", updated_time=1234,
        )
        note = store.get_note(NID)
        self.assertEqual(note.title, "T")
        self.assertEqual(note.tags, ("a", "x"))  # stored sorted
        self.assertEqual(note.updated_time, 1234)
        self.assertEqual(len(note.hashes.combined), 64)
        store.delete_note(NID)
        self.assertIsNone(store.get_note(NID))

    def test_missing_db_requires_init(self):
        with self.assertRaises(WorkspaceError) as ctx:
            StateDB(self.path).connect()
        self.assertIn("init", str(ctx.exception))

    def test_corrupt_db_detected(self):
        self.path.write_bytes(b"this is definitely not a sqlite database " * 100)
        with self.assertRaises(WorkspaceError) as ctx:
            StateDB(self.path).connect()
        self.assertIn("corrupt", str(ctx.exception).lower())

    def test_newer_schema_rejected(self):
        store = self.open_store(create=True)
        store.set_meta("state_schema_version", "999")
        store.close()
        with self.assertRaises(WorkspaceError) as ctx:
            StateDB(self.path).connect()
        self.assertIn("newer", str(ctx.exception))
        self.path.unlink()  # Windows proves the failed connection was closed.

    def test_migration_path_applied(self):
        store = self.open_store(create=True)
        store.set_meta("state_schema_version", "0")
        store.close()
        calls = []

        def migrate_0_to_1(conn):
            calls.append("0->1")

        MIGRATIONS[0] = migrate_0_to_1
        try:
            store = self.open_store()
            self.assertEqual(calls, ["0->1"])
            self.assertEqual(store.get_meta("state_schema_version"), "1")
        finally:
            del MIGRATIONS[0]

    def test_missing_migration_fails_cleanly(self):
        store = self.open_store(create=True)
        store.set_meta("state_schema_version", "0")
        store.close()
        with self.assertRaises(WorkspaceError) as ctx:
            StateDB(self.path).connect()
        self.assertIn("no migration path", str(ctx.exception))
        self.path.unlink()  # Windows proves the failed connection was closed.

    def test_move_prefix(self):
        store = self.open_store(create=True)
        store.upsert_folder(folder_id="f" * 32, rel_path="Old", title="Old", parent_id="")
        store.upsert_folder(folder_id="e" * 32, rel_path="Old/Sub", title="Sub", parent_id="f" * 32)
        store.upsert_note(
            note_id=NID, rel_path="Old/Sub/n--aaaaaaaa.md", title="T", body="b",
            tags=(), parent_id="e" * 32, updated_time=1,
        )
        store.upsert_note(
            note_id="b" * 32, rel_path="Older/x.md", title="X", body="b",
            tags=(), parent_id="f" * 32, updated_time=1,
        )
        store.move_prefix("Old", "New")
        self.assertEqual(store.all_folders()["f" * 32].rel_path, "New")
        self.assertEqual(store.all_folders()["e" * 32].rel_path, "New/Sub")
        self.assertEqual(store.get_note(NID).rel_path, "New/Sub/n--aaaaaaaa.md")
        # Prefix match must not catch sibling "Older/".
        self.assertEqual(store.get_note("b" * 32).rel_path, "Older/x.md")

    def test_conflicts_and_runs(self):
        store = self.open_store(create=True)
        store.add_conflict(conflict_id="c1", note_id=NID, rel_path="Work/a.md", category="divergent_edit")
        self.assertEqual(len(store.open_conflicts()), 1)
        self.assertIsNotNone(store.open_conflict_for_note(NID))
        store.set_conflict_status("c1", "resolved")
        self.assertEqual(store.open_conflicts(), [])
        store.record_run(run_id="r1", command="push", journal_path="/tmp/j.json")
        self.assertEqual(len(store.incomplete_runs()), 1)
        store.finish_run("r1", "complete")
        self.assertEqual(store.incomplete_runs(), [])


if __name__ == "__main__":
    unittest.main()
