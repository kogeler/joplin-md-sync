import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.errors import WorkspaceLockedError
from joplin_md_sync.locking import WorkspaceLock

SRC = str(Path(__file__).resolve().parents[2] / "src")


class LockingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "lock"

    def test_acquire_release_reacquire(self):
        lock = WorkspaceLock(self.lock_path)
        lock.acquire()
        lock.release()
        lock2 = WorkspaceLock(self.lock_path)
        lock2.acquire()
        lock2.release()

    def test_second_process_blocked(self):
        """A different process must fail to acquire while we hold the lock."""
        with WorkspaceLock(self.lock_path):
            script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {SRC!r})
                from joplin_md_sync.locking import WorkspaceLock
                from joplin_md_sync.errors import WorkspaceLockedError
                try:
                    WorkspaceLock(__import__("pathlib").Path({str(self.lock_path)!r})).acquire()
                except WorkspaceLockedError:
                    sys.exit(42)
                sys.exit(0)
                """
            )
            proc = subprocess.run([sys.executable, "-c", script], timeout=30)
            self.assertEqual(proc.returncode, 42, "child process acquired a held lock")

    def test_released_lock_acquirable_by_other_process(self):
        with WorkspaceLock(self.lock_path):
            pass
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {SRC!r})
            from joplin_md_sync.locking import WorkspaceLock
            WorkspaceLock(__import__("pathlib").Path({str(self.lock_path)!r})).acquire()
            sys.exit(0)
            """
        )
        proc = subprocess.run([sys.executable, "-c", script], timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_error_type(self):
        lock = WorkspaceLock(self.lock_path)
        lock.acquire()
        self.addCleanup(lock.release)
        self.assertTrue(issubclass(WorkspaceLockedError, Exception))


if __name__ == "__main__":
    unittest.main()
