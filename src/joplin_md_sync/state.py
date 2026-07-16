"""SQLite state database: base snapshots, tombstones, conflicts, run records.

The database is authoritative for synchronization history. Full base
snapshots (title, body, tags, parent) are stored so a true three-way
comparison is always possible without contacting Joplin.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from joplin_md_sync import STATE_SCHEMA_VERSION
from joplin_md_sync.canonical import note_hashes
from joplin_md_sync.errors import WorkspaceError
from joplin_md_sync.models import BaseFolder, BaseNote

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    rel_path TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    updated_time INTEGER NOT NULL,
    title_hash TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    tags_hash TEXT NOT NULL,
    parent_hash TEXT NOT NULL,
    combined_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    rel_path TEXT NOT NULL,
    title TEXT NOT NULL,
    parent_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tombstones (
    note_id TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    title TEXT NOT NULL,
    created_time INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    category TEXT NOT NULL,
    created_time INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS journal_runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    started_time INTEGER NOT NULL,
    status TEXT NOT NULL,
    journal_path TEXT NOT NULL
);
"""

# Registry of schema migrations: from_version -> callable(connection).
# Future releases append entries; each migration bumps the stored version by 1.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


class StateDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    # --- lifecycle -------------------------------------------------------

    def connect(self, *, create: bool = False) -> sqlite3.Connection:
        if not create and not self.path.exists():
            raise WorkspaceError(
                f"state database not found: {self.path}; run 'joplin-md-sync init' first"
            )
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            if create:
                conn.executescript(_SCHEMA)
                cur = conn.execute("SELECT value FROM meta WHERE key='state_schema_version'")
                if cur.fetchone() is None:
                    conn.execute(
                        "INSERT INTO meta(key, value) VALUES('state_schema_version', ?)",
                        (str(STATE_SCHEMA_VERSION),),
                    )
                conn.commit()
            self._check_integrity(conn)
            self._migrate(conn)
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise WorkspaceError(
                f"state database is corrupt or unreadable ({self.path}): {exc}; "
                "restore it from a backup or re-run init in a fresh directory"
            ) from exc
        return conn

    def _check_integrity(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise WorkspaceError(
                f"state database failed integrity check: {self.path}; "
                "restore from backup or re-initialize the workspace"
            )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT value FROM meta WHERE key='state_schema_version'").fetchone()
        if row is None:
            raise WorkspaceError(f"state database has no schema version: {self.path}")
        version = int(row[0])
        if version > STATE_SCHEMA_VERSION:
            raise WorkspaceError(
                f"state database schema version {version} is newer than this tool "
                f"supports ({STATE_SCHEMA_VERSION}); upgrade joplin-md-sync"
            )
        while version < STATE_SCHEMA_VERSION:
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise WorkspaceError(
                    f"no migration path from state schema {version} to {STATE_SCHEMA_VERSION}"
                )
            migration(conn)
            version += 1
            conn.execute(
                "UPDATE meta SET value=? WHERE key='state_schema_version'", (str(version),)
            )
            conn.commit()


class StateStore:
    """High-level typed access to one open state database connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        self.conn.close()

    # --- meta -------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- base notes ---------------------------------------------------------

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> BaseNote:
        tags = tuple(json.loads(row["tags"]))
        return BaseNote(
            id=row["id"],
            rel_path=row["rel_path"],
            title=row["title"],
            body=row["body"],
            tags=tags,
            parent_id=row["parent_id"],
            updated_time=row["updated_time"],
            hashes=note_hashes(row["title"], row["body"], tags, row["parent_id"]),
        )

    def all_notes(self) -> dict[str, BaseNote]:
        rows = self.conn.execute("SELECT * FROM notes").fetchall()
        return {row["id"]: self._row_to_note(row) for row in rows}

    def get_note(self, note_id: str) -> BaseNote | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return None if row is None else self._row_to_note(row)

    def upsert_note(
        self,
        *,
        note_id: str,
        rel_path: str,
        title: str,
        body: str,
        tags: tuple[str, ...],
        parent_id: str,
        updated_time: int,
    ) -> None:
        hashes = note_hashes(title, body, tags, parent_id)
        self.conn.execute(
            "INSERT INTO notes(id, rel_path, title, body, tags, parent_id, updated_time,"
            " title_hash, body_hash, tags_hash, parent_hash, combined_hash)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET rel_path=excluded.rel_path, title=excluded.title,"
            " body=excluded.body, tags=excluded.tags, parent_id=excluded.parent_id,"
            " updated_time=excluded.updated_time, title_hash=excluded.title_hash,"
            " body_hash=excluded.body_hash, tags_hash=excluded.tags_hash,"
            " parent_hash=excluded.parent_hash, combined_hash=excluded.combined_hash",
            (
                note_id,
                rel_path,
                title,
                body,
                json.dumps(sorted(tags), ensure_ascii=False),
                parent_id,
                updated_time,
                hashes.title,
                hashes.body,
                hashes.tags,
                hashes.parent,
                hashes.combined,
            ),
        )
        self.conn.commit()

    def delete_note(self, note_id: str) -> None:
        self.conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.conn.commit()

    # --- base folders ---------------------------------------------------------

    def all_folders(self) -> dict[str, BaseFolder]:
        rows = self.conn.execute("SELECT * FROM folders").fetchall()
        return {
            row["id"]: BaseFolder(
                id=row["id"],
                rel_path=row["rel_path"],
                title=row["title"],
                parent_id=row["parent_id"],
            )
            for row in rows
        }

    def upsert_folder(self, *, folder_id: str, rel_path: str, title: str, parent_id: str) -> None:
        self.conn.execute(
            "INSERT INTO folders(id, rel_path, title, parent_id) VALUES(?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET rel_path=excluded.rel_path,"
            " title=excluded.title, parent_id=excluded.parent_id",
            (folder_id, rel_path, title, parent_id),
        )
        self.conn.commit()

    def delete_folder(self, folder_id: str) -> None:
        self.conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        self.conn.commit()

    def move_prefix(self, old_prefix: str, new_prefix: str) -> None:
        """Rewrite stored rel_paths after a directory rename/move."""
        like = old_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        start = len(old_prefix) + 1  # substr() is 1-based
        for table in ("notes", "folders"):
            self.conn.execute(
                f"UPDATE {table} SET rel_path = ? || substr(rel_path, ?) "
                "WHERE rel_path LIKE ? ESCAPE '\\'",
                (new_prefix + "/", start + 1, like),
            )
            self.conn.execute(
                f"UPDATE {table} SET rel_path = ? WHERE rel_path = ?",
                (new_prefix, old_prefix),
            )
        self.conn.commit()

    # --- tombstones ---------------------------------------------------------

    def add_tombstone(self, *, note_id: str, side: str, rel_path: str, title: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO tombstones(note_id, side, rel_path, title, created_time)"
            " VALUES(?,?,?,?,?)",
            (note_id, side, rel_path, title, int(time.time() * 1000)),
        )
        self.conn.commit()

    def remove_tombstone(self, note_id: str) -> None:
        self.conn.execute("DELETE FROM tombstones WHERE note_id=?", (note_id,))
        self.conn.commit()

    def all_tombstones(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM tombstones ORDER BY note_id").fetchall()

    # --- conflicts ---------------------------------------------------------

    def add_conflict(self, *, conflict_id: str, note_id: str, rel_path: str, category: str) -> None:
        self.conn.execute(
            "INSERT INTO conflicts(id, note_id, rel_path, category, created_time, status)"
            " VALUES(?,?,?,?,?,'open')",
            (conflict_id, note_id, rel_path, category, int(time.time() * 1000)),
        )
        self.conn.commit()

    def get_conflict(self, conflict_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()

    def open_conflicts(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM conflicts WHERE status='open' ORDER BY created_time, id"
        ).fetchall()

    def open_conflict_for_note(self, note_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM conflicts WHERE note_id=? AND status='open'", (note_id,)
        ).fetchone()

    def set_conflict_status(self, conflict_id: str, status: str) -> None:
        self.conn.execute("UPDATE conflicts SET status=? WHERE id=?", (status, conflict_id))
        self.conn.commit()

    # --- journal runs ---------------------------------------------------------

    def record_run(self, *, run_id: str, command: str, journal_path: str) -> None:
        self.conn.execute(
            "INSERT INTO journal_runs(run_id, command, started_time, status, journal_path)"
            " VALUES(?,?,?,?,?)",
            (run_id, command, int(time.time() * 1000), "in-progress", journal_path),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, status: str) -> None:
        self.conn.execute("UPDATE journal_runs SET status=? WHERE run_id=?", (status, run_id))
        self.conn.commit()

    def incomplete_runs(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM journal_runs WHERE status='in-progress' ORDER BY started_time"
        ).fetchall()
