"""Workspace layout, scanning, and safe filesystem primitives.

All relative paths are stored POSIX-style (forward slashes) regardless of
platform. Symlinks are never followed; anything resolving outside the
workspace root is rejected.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from joplin_md_sync import STATE_SCHEMA_VERSION, __version__
from joplin_md_sync.canonical import canonicalize_body
from joplin_md_sync.errors import WorkspaceError
from joplin_md_sync.metadata import MetadataError, has_header, parse_note_file
from joplin_md_sync.models import InvalidLocalFile, LocalFolderDir, LocalNoteFile
from joplin_md_sync.paths import is_within_root
from joplin_md_sync.state import StateDB, StateStore

SYNC_DIR = ".joplin-sync"
FOLDER_META = ".joplin-folder.json"
WORKSPACE_CONFIG = "workspace.json"
WORKSPACE_SCHEMA_VERSION = 1

_WORKSPACE_GITIGNORE = """# joplin-md-sync internal state (never commit)
.joplin-sync/
"""


@dataclass
class LocalScan:
    """Everything found on disk during one workspace scan."""

    folders_by_id: dict[str, LocalFolderDir] = field(default_factory=dict)
    folders_by_path: dict[str, LocalFolderDir] = field(default_factory=dict)
    candidate_folders: list[LocalFolderDir] = field(default_factory=list)  # no id yet
    notes: list[LocalNoteFile] = field(default_factory=list)
    invalid: list[InvalidLocalFile] = field(default_factory=list)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.sync_dir = self.root / SYNC_DIR
        self.state_path = self.sync_dir / "state.sqlite3"
        self.lock_path = self.sync_dir / "lock"
        self.journal_dir = self.sync_dir / "journal"
        self.backups_dir = self.sync_dir / "backups"
        self.conflicts_dir = self.sync_dir / "conflicts"
        self.quarantine_dir = self.sync_dir / "quarantine"
        self.resources_dir = self.sync_dir / "resources"
        self.config_path = self.sync_dir / WORKSPACE_CONFIG

    # --- lifecycle -------------------------------------------------------

    @classmethod
    def create(cls, root: Path, *, mode: str) -> Workspace:
        ws = cls(root)
        if ws.state_path.exists():
            raise WorkspaceError(f"workspace already initialized: {ws.root}")
        ws.root.mkdir(parents=True, exist_ok=True)
        for d in (ws.sync_dir, ws.journal_dir, ws.backups_dir, ws.conflicts_dir,
                  ws.quarantine_dir, ws.resources_dir):
            d.mkdir(parents=True, exist_ok=True)
        config = {
            "schema": WORKSPACE_SCHEMA_VERSION,
            "mode": mode,
            "created_time": int(time.time() * 1000),
            "created_by": f"joplin-md-sync {__version__}",
            "options": {"backup_retention": 10},
        }
        write_file_atomic(ws.config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")
        gitignore = ws.root / ".gitignore"
        if not gitignore.exists():
            write_file_atomic(gitignore, _WORKSPACE_GITIGNORE)
        StateDB(ws.state_path).connect(create=True).close()
        return ws

    @classmethod
    def load(cls, root: Path) -> Workspace:
        ws = cls(root)
        if not ws.root.is_dir():
            raise WorkspaceError(f"workspace root does not exist: {ws.root}")
        if not ws.config_path.is_file() or not ws.state_path.is_file():
            raise WorkspaceError(
                f"not an initialized joplin-md-sync workspace: {ws.root}; "
                "run 'joplin-md-sync init --root PATH' first"
            )
        return ws

    def read_config(self) -> dict[str, object]:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"unreadable workspace config {self.config_path}: {exc}") from exc
        if not isinstance(config, dict):
            raise WorkspaceError(f"workspace config must be a JSON object: {self.config_path}")
        schema = config.get("schema")
        if schema != WORKSPACE_SCHEMA_VERSION:
            raise WorkspaceError(
                f"unsupported workspace schema {schema!r} (expected {WORKSPACE_SCHEMA_VERSION})"
            )
        return config

    def write_config(self, config: dict[str, object]) -> None:
        write_file_atomic(self.config_path, json.dumps(config, indent=2, sort_keys=True) + "\n")

    def open_state(self, *, expected_schema: int = STATE_SCHEMA_VERSION) -> StateStore:
        conn = StateDB(self.state_path).connect()
        return StateStore(conn)

    # --- scanning -------------------------------------------------------

    def scan(self) -> LocalScan:
        """Walk the workspace and parse every managed directory and note."""
        scan = LocalScan()
        self._scan_dir(self.root, parent_folder_id="", rel_prefix="", scan=scan, depth=0)
        return scan

    def _scan_dir(
        self, directory: Path, *, parent_folder_id: str, rel_prefix: str, scan: LocalScan, depth: int
    ) -> None:
        if depth > 32:
            scan.invalid.append(InvalidLocalFile(rel_path=rel_prefix, reason="directory nesting too deep"))
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            scan.invalid.append(InvalidLocalFile(rel_path=rel_prefix or ".", reason=f"unreadable directory: {exc}"))
            return

        for entry in entries:
            name = entry.name
            rel = f"{rel_prefix}{name}"
            if name == SYNC_DIR or (name.startswith(".") and name != FOLDER_META):
                continue
            if entry.is_symlink():
                scan.invalid.append(InvalidLocalFile(rel_path=rel, reason="symlinks are not followed"))
                continue
            if not is_within_root(self.root, entry):
                scan.invalid.append(InvalidLocalFile(rel_path=rel, reason="path resolves outside the workspace root"))
                continue

            if entry.is_dir():
                folder = self._read_folder_meta(entry, rel, parent_folder_id, scan)
                if folder is None:
                    # Invalid .joplin-folder.json already reported; do not descend.
                    continue
                if folder.folder_id is None:
                    scan.candidate_folders.append(folder)
                else:
                    if folder.folder_id in scan.folders_by_id:
                        scan.invalid.append(
                            InvalidLocalFile(
                                rel_path=rel,
                                reason=f"duplicate folder id {folder.folder_id} "
                                f"(also at {scan.folders_by_id[folder.folder_id].rel_path})",
                            )
                        )
                        continue
                    scan.folders_by_id[folder.folder_id] = folder
                scan.folders_by_path[rel] = folder
                self._scan_dir(
                    entry,
                    parent_folder_id=folder.folder_id or "",
                    rel_prefix=rel + "/",
                    scan=scan,
                    depth=depth + 1,
                )
            elif entry.is_file() and name.lower().endswith(".md"):
                if rel_prefix == "":
                    scan.invalid.append(
                        InvalidLocalFile(
                            rel_path=rel,
                            reason="Markdown files must live inside a notebook directory, not the workspace root",
                        )
                    )
                    continue
                self._read_note_file(entry, rel, parent_folder_id, scan)
            # Other file types are intentionally ignored (agent scratch files etc.).

    def _read_folder_meta(
        self, directory: Path, rel: str, parent_folder_id: str, scan: LocalScan
    ) -> LocalFolderDir | None:
        meta_path = directory / FOLDER_META
        if not meta_path.is_file():
            # Candidate new notebook: title derives from the directory name.
            return LocalFolderDir(rel_path=rel, folder_id=None, title=directory.name, parent_id=parent_folder_id)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("must be a JSON object")
            folder_id = meta["id"]
            title = meta["title"]
            if meta.get("schema") != 1:
                raise ValueError(f"unsupported folder schema {meta.get('schema')!r}")
            if not isinstance(folder_id, str) or not isinstance(title, str):
                raise ValueError("'id' and 'title' must be strings")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            scan.invalid.append(
                InvalidLocalFile(rel_path=f"{rel}/{FOLDER_META}", reason=f"invalid folder metadata: {exc}")
            )
            return None
        return LocalFolderDir(rel_path=rel, folder_id=folder_id, title=title, parent_id=parent_folder_id)

    def _read_note_file(self, path: Path, rel: str, parent_folder_id: str, scan: LocalScan) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            scan.invalid.append(InvalidLocalFile(rel_path=rel, reason=f"unreadable file: {exc}"))
            return
        if not has_header(text):
            # A headerless Markdown file is a new local note: title = file stem.
            scan.notes.append(
                LocalNoteFile(
                    rel_path=rel,
                    note_id=None,
                    title=path.stem,
                    body=canonicalize_body(text),
                    tags=(),
                    parent_id=parent_folder_id,
                    has_header=False,
                )
            )
            return
        try:
            parsed = parse_note_file(text)
        except MetadataError as exc:
            scan.invalid.append(InvalidLocalFile(rel_path=rel, reason=str(exc)))
            return
        scan.notes.append(
            LocalNoteFile(
                rel_path=rel,
                note_id=parsed.note_id,
                title=parsed.title,
                body=parsed.body,
                tags=parsed.tags,
                parent_id=parent_folder_id,
            )
        )

    # --- filesystem primitives -------------------------------------------

    def abs_path(self, rel_path: str) -> Path:
        path = self.root.joinpath(*rel_path.split("/"))
        if not is_within_root(self.root, path.parent):
            raise WorkspaceError(f"path escapes the workspace root: {rel_path}")
        return path

    def backup_file(self, rel_path: str, run_id: str) -> None:
        src = self.abs_path(rel_path)
        if not src.is_file():
            return
        dst = self.backups_dir / run_id / Path(*rel_path.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def quarantine_file(self, rel_path: str, run_id: str) -> str:
        """Move a local file into the recoverable quarantine; returns the new path."""
        src = self.abs_path(rel_path)
        dst = self.quarantine_dir / run_id / Path(*rel_path.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        return str(dst.relative_to(self.root).as_posix())

    def prune_backups(self, retention: int) -> None:
        for base in (self.backups_dir, self.quarantine_dir):
            if not base.is_dir():
                continue
            runs = sorted(base.iterdir(), key=lambda p: p.stat().st_mtime)
            for old in runs[:-retention] if retention > 0 else []:
                shutil.rmtree(old, ignore_errors=True)


def write_file_atomic(path: Path, content: str) -> None:
    """Write UTF-8 text durably: temp file in the same directory + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".jms-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".jms-tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
