"""Race protection, ambiguous writes, journal recovery, locking, migration."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.helpers import WorkspaceTestCase


class RaceProtectionTest(WorkspaceTestCase):
    def test_remote_change_between_plan_and_put(self):
        """The pre-write guard re-reads the note and aborts on drift."""
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nlocal push attempt\n", encoding="utf-8")

        store = self.server.store
        nid = self.note_k8s
        state = {"mutated": False}

        def hook(method, path, query):
            # Planning phase lists notes; when the guard re-reads this note
            # before the PUT, a concurrent editor changes it first.
            if method == "GET" and path == f"/notes/{nid}" and not state["mutated"]:
                state["mutated"] = True
                store.notes[nid]["body"] = "concurrent remote edit\n"
                store.notes[nid]["updated_time"] = store.tick()
            return None

        # Arm the hook only after the snapshot phase: planning itself calls
        # GET /notes/:id. Run a diff first so the base is settled, then arm.
        self.server.set_before_request(hook)
        result = self.cli("push", "--root", str(self.root), "--json", expect=5)
        self.server.set_before_request(None)

        self.assertEqual(result.json["code"], "CONCURRENT_MODIFICATION")
        # The local edit is intact and the remote concurrent edit survived.
        self.assertIn("local push attempt", note.read_text(encoding="utf-8"))
        self.assertEqual(store.notes[nid]["body"], "concurrent remote edit\n")

    def test_local_change_between_plan_and_apply(self):
        """Pulling over a file that changed after planning must abort."""
        self.init_and_pull()
        self.server.store.notes[self.note_k8s]["body"] = "remote v2\n"
        self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
        note = self.find_note_file("Kubernetes")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]

        state = {"done": False}

        def hook(method, path, query):
            # The executor re-reads the remote note (guard) right before
            # rewriting the local file; sneak in a local edit at that moment.
            if method == "GET" and path == f"/notes/{self.note_k8s}" and not state["done"]:
                state["done"] = True
                note.write_text(header + "\n\nlocal sneak edit\n", encoding="utf-8")
            return None

        self.server.set_before_request(hook)
        result = self.cli("pull", "--root", str(self.root), "--json", expect=5)
        self.server.set_before_request(None)
        self.assertEqual(result.json["code"], "CONCURRENT_MODIFICATION")
        self.assertIn("local sneak edit", note.read_text(encoding="utf-8"))

    def test_read_timeout_fails_cleanly_before_write(self):
        self.init_and_pull()
        self.server.set_before_request(lambda m, p, q: "abort" if m == "GET" and p == "/notes" else None)
        result = self.cli("pull", "--root", str(self.root), "--json", expect=4)
        self.server.set_before_request(None)
        self.assertEqual(result.json["code"], "API_UNAVAILABLE")

    def test_ambiguous_write_applied_is_detected(self):
        """A PUT whose response is lost but which was applied counts as applied."""
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nnew body after timeout\n", encoding="utf-8")

        state = {"aborted": False}

        def hook(method, path, query):
            if method == "PUT" and path == f"/notes/{self.note_k8s}" and not state["aborted"]:
                state["aborted"] = True
                # Apply the write manually, then kill the response.
                self.server.store.notes[self.note_k8s]["body"] = "new body after timeout\n"
                self.server.store.notes[self.note_k8s]["updated_time"] = self.server.store.tick()
                return "abort"
            return None

        self.server.set_before_request(hook)
        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.server.set_before_request(None)
        self.assertEqual(result.json["execution"]["applied"], 1)
        self.cli("diff", "--root", str(self.root), "--json", "--exit-code", expect=0)

    def test_ambiguous_write_not_applied_reports_failure(self):
        self.init_and_pull()
        note = self.find_note_file("Kubernetes")
        header = note.read_text(encoding="utf-8").split("\n", 1)[0]
        note.write_text(header + "\n\nnever arrives\n", encoding="utf-8")

        state = {"aborted": False}

        def hook(method, path, query):
            if method == "PUT" and not state["aborted"]:
                state["aborted"] = True
                return "abort"  # write lost entirely
            return None

        self.server.set_before_request(hook)
        result = self.cli("push", "--root", str(self.root), "--json", expect=6)
        self.server.set_before_request(None)
        self.assertEqual(result.json["code"], "PARTIAL_FAILURE")
        ops = result.json["execution"]["operations"]
        self.assertEqual(ops[0]["result"], "failed")
        # State was not corrupted: a rerun pushes successfully.
        result = self.cli("push", "--root", str(self.root), "--json", expect=0)
        self.assertIn("never arrives", self.server.store.notes[self.note_k8s]["body"])


class LockingIntegrationTest(WorkspaceTestCase):
    def test_second_process_fails_clearly_while_locked(self):
        self.init_and_pull()
        import subprocess
        import sys as _sys
        import textwrap

        src = str(Path(__file__).resolve().parents[2] / "src")
        lock_path = self.root / ".joplin-sync" / "lock"
        script = textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {src!r})
            from joplin_md_sync.locking import WorkspaceLock
            lock = WorkspaceLock(__import__("pathlib").Path({str(lock_path)!r}))
            lock.acquire()
            print("locked", flush=True)
            time.sleep(20)
            """
        )
        proc = subprocess.Popen(
            [_sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
        )
        try:
            assert proc.stdout is not None
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            result = self.cli("status", "--root", str(self.root), "--json", expect=5)
            self.assertEqual(result.json["code"], "WORKSPACE_LOCKED")
        finally:
            proc.kill()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


class RecoveryTest(WorkspaceTestCase):
    def _simulate_crash(self) -> str:
        """Leave an in-progress journal behind, as a killed process would."""
        self.init_and_pull()
        sync_dir = self.root / ".joplin-sync"
        run_id = "deadbeef" * 4
        journal = {
            "run_id": run_id, "tool_version": "1.0.0", "command": "push",
            "started_time": 0, "status": "in-progress",
            "operations": [
                {"op_id": "op-0001", "kind": "push_update_remote", "status": "planned",
                 "note_id": self.note_k8s, "path": "Work/x.md",
                 "expected_local_hash": "0" * 64, "expected_remote_hash": "0" * 64},
            ],
        }
        (sync_dir / "journal" / f"{run_id}.json").write_text(
            json.dumps(journal), encoding="utf-8"
        )
        import sqlite3

        conn = sqlite3.connect(sync_dir / "state.sqlite3")
        conn.execute(
            "INSERT INTO journal_runs(run_id, command, started_time, status, journal_path)"
            " VALUES(?, 'push', 0, 'in-progress', ?)",
            (run_id, str(sync_dir / "journal" / f"{run_id}.json")),
        )
        conn.commit()
        conn.close()
        return run_id

    def test_incomplete_journal_blocks_mutations(self):
        self._simulate_crash()
        result = self.cli("push", "--root", str(self.root), "--json", expect=6)
        self.assertEqual(result.json["code"], "RECOVERY_REQUIRED")
        result = self.cli("sync", "--root", str(self.root), "--json", expect=6)
        self.assertEqual(result.json["code"], "RECOVERY_REQUIRED")
        # doctor reports it too.
        result = self.cli("doctor", "--root", str(self.root), "--offline", "--json", expect=6)

    def test_recover_settles_and_unblocks(self):
        run_id = self._simulate_crash()
        result = self.cli("recover", "--root", str(self.root), "--json", expect=0)
        recovered = result.json["recovered_runs"]
        self.assertEqual(recovered[0]["run_id"], run_id)
        self.assertEqual(
            recovered[0]["operations_settled"][0]["status"], "skipped"
        )  # not applied -> rerun
        # Mutations work again.
        self.cli("push", "--root", str(self.root), "--json", expect=0)
        # Journal file records the recovery.
        journal = json.loads(
            (self.root / ".joplin-sync" / "journal" / f"{run_id}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(journal["status"], "recovered")

    def test_recover_marks_actually_applied_ops(self):
        """An op whose base snapshot was committed counts as applied."""
        self.init_and_pull()
        # Take the real base hash of the Kubernetes note as "intended".
        import sqlite3

        conn = sqlite3.connect(self.root / ".joplin-sync" / "state.sqlite3")
        combined = conn.execute(
            "SELECT combined_hash FROM notes WHERE id=?", (self.note_k8s,)
        ).fetchone()[0]
        run_id = "cafebabe" * 4
        journal = {
            "run_id": run_id, "command": "pull", "status": "in-progress", "started_time": 0,
            "tool_version": "1.0.0",
            "operations": [
                {"op_id": "op-0001", "kind": "pull_update_local", "status": "planned",
                 "note_id": self.note_k8s, "expected_remote_hash": combined,
                 "expected_local_hash": None},
            ],
        }
        jpath = self.root / ".joplin-sync" / "journal" / f"{run_id}.json"
        jpath.write_text(json.dumps(journal), encoding="utf-8")
        conn.execute(
            "INSERT INTO journal_runs(run_id, command, started_time, status, journal_path)"
            " VALUES(?, 'pull', 0, 'in-progress', ?)", (run_id, str(jpath)),
        )
        conn.commit()
        conn.close()
        result = self.cli("recover", "--root", str(self.root), "--json", expect=0)
        settled = result.json["recovered_runs"][0]["operations_settled"][0]
        self.assertEqual(settled["status"], "applied")


class CorruptStateTest(WorkspaceTestCase):
    def test_corrupt_database_detected(self):
        self.init_and_pull()
        (self.root / ".joplin-sync" / "state.sqlite3").write_bytes(b"garbage" * 1000)
        result = self.cli("pull", "--root", str(self.root), "--json", expect=3)
        self.assertIn("corrupt", result.json["error"].lower())
        result = self.cli("doctor", "--root", str(self.root), "--offline", "--json", expect=3)


if __name__ == "__main__":
    import unittest

    unittest.main()
