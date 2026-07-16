"""Shared data structures for notes, folders, classification, and planning."""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Item status codes (public contract, see docs/STATE_MODEL.md) ----------

UNCHANGED = "UNCHANGED"
LOCAL_MODIFIED = "LOCAL_MODIFIED"
REMOTE_MODIFIED = "REMOTE_MODIFIED"
BOTH_IDENTICAL = "BOTH_IDENTICAL"
CONFLICT = "CONFLICT"
LOCAL_NEW = "LOCAL_NEW"
REMOTE_NEW = "REMOTE_NEW"
LOCAL_DELETED = "LOCAL_DELETED"
REMOTE_DELETED = "REMOTE_DELETED"
BOTH_DELETED = "BOTH_DELETED"
DELETE_CONFLICT = "DELETE_CONFLICT"
MOVED_LOCAL = "MOVED_LOCAL"
MOVED_REMOTE = "MOVED_REMOTE"
METADATA_MODIFIED = "METADATA_MODIFIED"
INVALID_LOCAL_FILE = "INVALID_LOCAL_FILE"
JOPLIN_CONFLICT_NOTE = "JOPLIN_CONFLICT_NOTE"

# Folder-level statuses.
FOLDER_LOCAL_NEW = "FOLDER_LOCAL_NEW"
FOLDER_REMOTE_NEW = "FOLDER_REMOTE_NEW"
FOLDER_LOCAL_MODIFIED = "FOLDER_LOCAL_MODIFIED"
FOLDER_REMOTE_MODIFIED = "FOLDER_REMOTE_MODIFIED"
FOLDER_REMOTE_DELETED = "FOLDER_REMOTE_DELETED"
FOLDER_LOCAL_DELETED = "FOLDER_LOCAL_DELETED"
FOLDER_CONFLICT = "FOLDER_CONFLICT"


@dataclass(frozen=True)
class NoteHashes:
    """Canonical SHA-256 hashes of the logical components of a note."""

    title: str
    body: str
    tags: str
    parent: str
    combined: str


@dataclass
class RemoteNote:
    """A note as seen through the Joplin Data API (canonicalized)."""

    id: str
    parent_id: str
    title: str
    body: str
    updated_time: int
    tags: tuple[str, ...] = ()
    is_conflict: bool = False
    deleted: bool = False


@dataclass
class RemoteFolder:
    id: str
    parent_id: str
    title: str


@dataclass
class LocalNoteFile:
    """A managed Markdown file found in the workspace."""

    rel_path: str
    note_id: str | None  # None => new local note
    title: str
    body: str  # canonicalized
    tags: tuple[str, ...]
    parent_id: str  # folder id of the containing directory
    has_header: bool = True


@dataclass
class InvalidLocalFile:
    rel_path: str
    reason: str


@dataclass
class LocalFolderDir:
    """A workspace directory with (or eligible for) a .joplin-folder.json."""

    rel_path: str
    folder_id: str | None  # None => new local notebook candidate
    title: str
    parent_id: str  # id of parent managed folder ("" at workspace root)


@dataclass
class BaseNote:
    """Base snapshot of a note from the last successful synchronization."""

    id: str
    rel_path: str
    title: str
    body: str
    tags: tuple[str, ...]
    parent_id: str
    updated_time: int
    hashes: NoteHashes


@dataclass
class BaseFolder:
    id: str
    rel_path: str
    title: str
    parent_id: str


@dataclass
class ItemState:
    """Classification result for one note across base/local/remote."""

    status: str
    note_id: str | None
    rel_path: str | None
    title: str | None = None
    changed_components: tuple[str, ...] = ()  # subset of title/body/tags/parent
    remote_changed_components: tuple[str, ...] = ()
    detail: str = ""
    base: BaseNote | None = None
    local: LocalNoteFile | None = None
    remote: RemoteNote | None = None

    def to_json(self) -> dict[str, object]:
        item: dict[str, object] = {
            "status": self.status,
            "note_id": self.note_id,
            "path": self.rel_path,
            "title": self.title,
        }
        if self.changed_components:
            item["local_changed"] = sorted(self.changed_components)
        if self.remote_changed_components:
            item["remote_changed"] = sorted(self.remote_changed_components)
        if self.detail:
            item["detail"] = self.detail
        return item


@dataclass
class FolderState:
    """Classification result for one notebook directory."""

    status: str
    folder_id: str | None
    rel_path: str | None
    title: str | None = None
    detail: str = ""
    base: BaseFolder | None = None
    local: LocalFolderDir | None = None
    remote: RemoteFolder | None = None

    def to_json(self) -> dict[str, object]:
        item: dict[str, object] = {
            "status": self.status,
            "folder_id": self.folder_id,
            "path": self.rel_path,
            "title": self.title,
        }
        if self.detail:
            item["detail"] = self.detail
        return item


# --- Operation plan ---------------------------------------------------------

# Operation kinds (stable identifiers used in journals and --json output).
OP_PULL_CREATE_LOCAL = "pull_create_local"
OP_PULL_UPDATE_LOCAL = "pull_update_local"
OP_PULL_DELETE_LOCAL = "pull_delete_local"  # quarantine, requires --propagate-deletes
OP_PULL_CREATE_DIR = "pull_create_dir"
OP_PULL_UPDATE_DIR = "pull_update_dir"  # rename/move/normalize a notebook dir
OP_PUSH_CREATE_REMOTE = "push_create_remote"
OP_PUSH_UPDATE_REMOTE = "push_update_remote"
OP_PUSH_DELETE_REMOTE = "push_delete_remote"  # trash, requires --propagate-deletes
OP_PUSH_CREATE_FOLDER = "push_create_folder"
OP_PUSH_UPDATE_FOLDER = "push_update_folder"
OP_REBASE = "rebase_base"  # both sides identical: update base snapshot only
OP_ADOPT_BASE = "adopt_base"  # local file with id matches remote; adopt as base
OP_CREATE_CONFLICT = "create_conflict"
OP_DROP_BASE = "drop_base"  # both sides deleted: forget the note
OP_NORMALIZE_LOCAL_PATH = "normalize_local_path"  # cosmetic rename to canonical name


@dataclass
class PlanOperation:
    """One planned, journaled, verifiable operation."""

    op_id: str
    kind: str
    note_id: str | None = None
    folder_id: str | None = None
    rel_path: str | None = None  # current local path (if any)
    new_rel_path: str | None = None  # target local path (moves/creates)
    fields: tuple[str, ...] = ()  # remote fields to PUT (push updates)
    detail: str = ""
    # Pre-state guards captured at planning time:
    expected_local_hash: str | None = None  # combined hash of L (or None if absent)
    expected_remote_hash: str | None = None  # combined hash of R (or None if absent)
    # References used by the executor:
    state: ItemState | None = None
    folder_state: FolderState | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"op_id": self.op_id, "kind": self.kind}
        if self.note_id:
            out["note_id"] = self.note_id
        if self.folder_id:
            out["folder_id"] = self.folder_id
        if self.rel_path:
            out["path"] = self.rel_path
        if self.new_rel_path and self.new_rel_path != self.rel_path:
            out["new_path"] = self.new_rel_path
        if self.fields:
            out["fields"] = sorted(self.fields)
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class OperationResult:
    op: PlanOperation
    status: str  # applied | skipped | failed
    detail: str = ""


@dataclass
class RemoteSnapshot:
    """Everything read from Joplin during one reconciliation scan."""

    folders: dict[str, RemoteFolder] = field(default_factory=dict)
    notes: dict[str, RemoteNote] = field(default_factory=dict)  # active notes only
    conflict_notes: tuple[tuple[str, str], ...] = ()  # (id, title)
    trashed_note_ids: frozenset[str] = frozenset()
    event_cursor: str | None = None
