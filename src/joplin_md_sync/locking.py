"""Exclusive cross-platform workspace lock.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Only one
mutating process may operate on a workspace at a time; read-only commands
take the same exclusive lock briefly (consistent reads cannot be guaranteed
while a mutator is running) and fail clearly when it is busy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType

from joplin_md_sync.errors import WorkspaceLockedError

if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import msvcrt

    def _lock_exclusive(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _lock_exclusive(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class WorkspaceLock:
    """Context manager holding the exclusive lock on ``.joplin-sync/lock``."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        if sys.platform == "win32":  # msvcrt needs at least one byte to lock
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
        if not _lock_exclusive(fd):
            os.close(fd)
            raise WorkspaceLockedError(
                f"workspace is locked by another joplin-md-sync process ({self.lock_path})"
            )
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> WorkspaceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
