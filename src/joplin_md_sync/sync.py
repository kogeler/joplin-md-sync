"""Synchronization engine: remote snapshot, plan execution, verification.

Execution model per operation (docs/STATE_MODEL.md):
1. re-read the side(s) that the plan was built from and confirm they still
   match the planned state (optimistic concurrency guard);
2. apply the operation;
3. re-read and verify the result;
4. only then commit the base snapshot;
5. record the outcome in the journal.

A guard failure marks the single operation as failed with
``CONCURRENT_MODIFICATION`` and never touches either side.
"""

from __future__ import annotations

import json
import logging
import posixpath
from dataclasses import dataclass, field
from typing import Any

from joplin_md_sync import models
from joplin_md_sync.api import AmbiguousWriteError, JoplinClient
from joplin_md_sync.canonical import canonicalize_body, canonicalize_tags, note_hashes
from joplin_md_sync.errors import ConcurrentModificationError, WorkspaceError
from joplin_md_sync.journal import (
    OP_APPLIED,
    OP_FAILED,
    OP_SKIPPED,
    RUN_COMPLETE,
    Journal,
)
from joplin_md_sync.metadata import emit_note_file, parse_note_file
from joplin_md_sync.models import (
    BaseNote,
    OperationResult,
    PlanOperation,
    RemoteFolder,
    RemoteNote,
    RemoteSnapshot,
)
from joplin_md_sync.paths import note_filename
from joplin_md_sync.state import StateStore
from joplin_md_sync.workspace import (
    FOLDER_META,
    LocalScan,
    Workspace,
    write_file_atomic,
)

log = logging.getLogger("joplin_md_sync.sync")


# --------------------------------------------------------------------------
# Remote snapshot
# --------------------------------------------------------------------------


def build_remote_snapshot(
    client: JoplinClient, base_notes: dict[str, BaseNote]
) -> RemoteSnapshot:
    """Read folders, notes, and the full tag map from Joplin.

    Bodies are fetched only for notes whose ``updated_time`` differs from the
    base snapshot (Joplin bumps it on every note change). Tag associations do
    NOT bump ``updated_time``, so tags are read independently via the tag map.
    """
    snapshot = RemoteSnapshot()

    for raw in client.list_folders():
        folder = RemoteFolder(
            id=raw["id"], parent_id=raw.get("parent_id", ""), title=raw.get("title", "")
        )
        snapshot.folders[folder.id] = folder

    # Tag map: tag title (lowercased) per note id.
    note_tags: dict[str, set[str]] = {}
    for tag in client.list_tags():
        title = (tag.get("title") or "").strip().lower()
        if not title:
            continue
        for ref in client.list_tag_notes(tag["id"]):
            note_tags.setdefault(ref["id"], set()).add(title)

    conflict_notes: list[tuple[str, str]] = []
    trashed: set[str] = set()
    for raw in client.list_notes(include_deleted=True, include_conflicts=True):
        nid = raw["id"]
        if raw.get("deleted_time"):
            trashed.add(nid)
            continue
        if raw.get("is_conflict"):
            conflict_notes.append((nid, raw.get("title", "")))
            continue
        base = base_notes.get(nid)
        updated_time = int(raw.get("updated_time", 0))
        if base is not None and base.updated_time == updated_time:
            body = base.body  # unchanged since last sync; avoid a body fetch
        else:
            full = client.get_note(nid)
            if full is None or full.get("deleted_time") or full.get("is_conflict"):
                # Disappeared between the two reads; treat as trashed.
                trashed.add(nid)
                continue
            body = canonicalize_body(full.get("body", ""))
            updated_time = int(full.get("updated_time", updated_time))
        snapshot.notes[nid] = RemoteNote(
            id=nid,
            parent_id=raw.get("parent_id", ""),
            title=raw.get("title", ""),
            body=body,
            updated_time=updated_time,
            tags=canonicalize_tags(note_tags.get(nid, set())),
        )

    snapshot.conflict_notes = tuple(sorted(conflict_notes))
    snapshot.trashed_note_ids = frozenset(trashed)

    try:  # best-effort: stored for a future /events-based optimization
        events = client.get_events()
        cursor = events.get("cursor") if isinstance(events, dict) else None
        snapshot.event_cursor = str(cursor) if cursor is not None else None
    except Exception:  # cursor is optional by design
        snapshot.event_cursor = None
    return snapshot


def fetch_remote_canonical(client: JoplinClient, note_id: str) -> RemoteNote | None:
    """Fetch one note plus its tags in canonical form; None when gone/trashed."""
    raw = client.get_note(note_id)
    if raw is None or raw.get("deleted_time") or raw.get("is_conflict"):
        return None
    tags = canonicalize_tags(t.get("title", "") for t in client.list_note_tags(note_id))
    return RemoteNote(
        id=note_id,
        parent_id=raw.get("parent_id", ""),
        title=raw.get("title", ""),
        body=canonicalize_body(raw.get("body", "")),
        updated_time=int(raw.get("updated_time", 0)),
        tags=tags,
    )


def reconcile_tags(
    client: JoplinClient, note_id: str, desired: tuple[str, ...],
    tag_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Attach/detach tags so the note's tag set equals ``desired``.

    Returns the (possibly extended) tag title -> id map for reuse.
    """
    if tag_map is None:
        tag_map = {
            (t.get("title") or "").strip().lower(): t["id"] for t in client.list_tags()
        }
    current_map = {
        (t.get("title") or "").strip().lower(): t["id"]
        for t in client.list_note_tags(note_id)
    }
    for title in desired:
        if title not in current_map:
            tid = tag_map.get(title)
            if tid is None:
                created = client.create_tag(title)
                tid = created["id"]
                tag_map[(created.get("title") or title).strip().lower()] = tid
            client.add_tag_to_note(tid, note_id)
    for title, tid in current_map.items():
        if title not in desired:
            client.remove_tag_from_note(tid, note_id)
    return tag_map


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@dataclass
class ExecutionReport:
    results: list[OperationResult] = field(default_factory=list)
    applied: int = 0
    failed: int = 0
    skipped: int = 0
    conflicts_created: int = 0
    concurrent_failures: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "failed": self.failed,
            "skipped": self.skipped,
            "conflicts_created": self.conflicts_created,
            "operations": [
                {**r.op.to_json(), "result": r.status,
                 **({"result_detail": r.detail} if r.detail else {})}
                for r in self.results
            ],
        }


class Executor:
    def __init__(
        self,
        workspace: Workspace,
        store: StateStore,
        client: JoplinClient,
        scan: LocalScan,
        journal: Journal,
    ) -> None:
        self.ws = workspace
        self.store = store
        self.client = client
        self.scan = scan
        self.journal = journal
        self.run_id = journal.run_id
        # rel-path prefix renames applied so far (dir moves during this run)
        self._renames: list[tuple[str, str]] = []
        # folders created during this run: rel_path -> folder id
        self._created_folders: dict[str, str] = {}
        self._tag_map: dict[str, str] | None = None  # tag title -> tag id

    # --- helpers ---------------------------------------------------------

    def current_path(self, rel_path: str) -> str:
        """Apply directory renames performed earlier in this run."""
        for old, new in self._renames:
            if rel_path == old:
                rel_path = new
            elif rel_path.startswith(old + "/"):
                rel_path = new + rel_path[len(old):]
        return rel_path

    def resolve_folder_id(self, dir_rel_path: str) -> str | None:
        if dir_rel_path in self._created_folders:
            return self._created_folders[dir_rel_path]
        folder = self.scan.folders_by_path.get(dir_rel_path)
        if folder is not None and folder.folder_id:
            return folder.folder_id
        return None

    def _read_local(self, rel_path: str) -> tuple[str, Any]:
        """Read and parse a managed file; returns (raw_text, ParsedNoteFile|None)."""
        path = self.ws.abs_path(rel_path)
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = parse_note_file(raw)
        except Exception:  # headerless new note or malformed
            parsed = None
        return raw, parsed

    def _guard_local(self, op: PlanOperation) -> None:
        """Confirm the local file still matches the planned state."""
        assert op.state is not None and op.state.local is not None
        local = op.state.local
        rel = self.current_path(op.rel_path or local.rel_path)
        try:
            raw, parsed = self._read_local(rel)
        except OSError as exc:
            raise ConcurrentModificationError(
                f"local file disappeared before apply: {rel} ({exc})"
            ) from exc
        if parsed is None:
            if local.has_header:
                raise ConcurrentModificationError(f"local file changed before apply: {rel}")
            title, body, tags = local.title, canonicalize_body(raw), ()
        else:
            title, body, tags = parsed.title, parsed.body, parsed.tags
        combined = note_hashes(title, body, tags, local.parent_id).combined
        if op.expected_local_hash is not None and combined != op.expected_local_hash:
            raise ConcurrentModificationError(
                f"local file changed between planning and apply: {rel}"
            )

    def _fetch_remote_canonical(self, note_id: str) -> RemoteNote | None:
        return fetch_remote_canonical(self.client, note_id)

    def _guard_remote(self, op: PlanOperation) -> RemoteNote | None:
        remote = self._fetch_remote_canonical(op.note_id) if op.note_id else None
        combined = (
            note_hashes(remote.title, remote.body, remote.tags, remote.parent_id).combined
            if remote
            else None
        )
        if combined != op.expected_remote_hash:
            raise ConcurrentModificationError(
                f"remote note changed between planning and apply: {op.note_id}"
            )
        return remote

    def _reconcile_tags(self, note_id: str, desired: tuple[str, ...]) -> None:
        self._tag_map = reconcile_tags(self.client, note_id, desired, self._tag_map)

    def _write_local_note(
        self, rel_path: str, note_id: str | None, title: str, tags: tuple[str, ...], body: str
    ) -> None:
        content = emit_note_file(note_id, title, tags, body)
        path = self.ws.abs_path(rel_path)
        if not path.parent.is_dir():
            raise WorkspaceError(f"target directory missing for {rel_path}")
        write_file_atomic(path, content)
        # Verify the write round-trips to the intended canonical state.
        parsed = parse_note_file(path.read_text(encoding="utf-8"))
        written = note_hashes(parsed.title, parsed.body, parsed.tags, "").combined
        intended = note_hashes(title, canonicalize_body(body), canonicalize_tags(tags), "").combined
        if written != intended:
            raise WorkspaceError(f"post-write verification failed for {rel_path}")

    # --- op handlers -------------------------------------------------------

    def execute(self, op: PlanOperation) -> OperationResult:
        handler = {
            models.OP_PULL_CREATE_DIR: self._op_pull_create_dir,
            models.OP_PULL_UPDATE_DIR: self._op_pull_update_dir,
            models.OP_PULL_CREATE_LOCAL: self._op_pull_create_local,
            models.OP_PULL_UPDATE_LOCAL: self._op_pull_update_local,
            models.OP_PULL_DELETE_LOCAL: self._op_pull_delete_local,
            models.OP_PUSH_CREATE_FOLDER: self._op_push_create_folder,
            models.OP_PUSH_UPDATE_FOLDER: self._op_push_update_folder,
            models.OP_PUSH_CREATE_REMOTE: self._op_push_create_remote,
            models.OP_PUSH_UPDATE_REMOTE: self._op_push_update_remote,
            models.OP_PUSH_DELETE_REMOTE: self._op_push_delete_remote,
            models.OP_REBASE: self._op_rebase,
            models.OP_ADOPT_BASE: self._op_rebase,
            models.OP_DROP_BASE: self._op_drop_base,
            models.OP_NORMALIZE_LOCAL_PATH: self._op_normalize_path,
            models.OP_CREATE_CONFLICT: self._op_create_conflict,
        }[op.kind]
        return handler(op)

    def _op_pull_create_dir(self, op: PlanOperation) -> OperationResult:
        fs = op.folder_state
        assert fs is not None and fs.remote is not None and op.new_rel_path
        rel = op.new_rel_path
        path = self.ws.abs_path(rel)
        path.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": fs.remote.id,
            "parent_id": fs.remote.parent_id,
            "schema": 1,
            "title": fs.remote.title,
        }
        write_file_atomic(path / FOLDER_META, json.dumps(meta, indent=2, sort_keys=True) + "\n")
        self.store.upsert_folder(
            folder_id=fs.remote.id, rel_path=rel, title=fs.remote.title,
            parent_id=fs.remote.parent_id,
        )
        return OperationResult(op, "applied")

    def _op_pull_update_dir(self, op: PlanOperation) -> OperationResult:
        fs = op.folder_state
        assert fs is not None and fs.remote is not None and op.rel_path and op.new_rel_path
        old_rel = self.current_path(op.rel_path)
        new_rel = op.new_rel_path
        old_path = self.ws.abs_path(old_rel)
        new_path = self.ws.abs_path(new_rel)
        if old_rel != new_rel:
            if new_path.exists():
                return OperationResult(
                    op, "failed", f"target already exists: {new_rel}"
                )
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
            self._renames.append((old_rel, new_rel))
            self.store.move_prefix(old_rel, new_rel)
        meta = {
            "id": fs.remote.id, "parent_id": fs.remote.parent_id,
            "schema": 1, "title": fs.remote.title,
        }
        write_file_atomic(new_path / FOLDER_META, json.dumps(meta, indent=2, sort_keys=True) + "\n")
        self.store.upsert_folder(
            folder_id=fs.remote.id, rel_path=new_rel, title=fs.remote.title,
            parent_id=fs.remote.parent_id,
        )
        return OperationResult(op, "applied")

    def _op_pull_create_local(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.remote is not None and op.new_rel_path
        remote = state.remote
        rel = self.current_path(op.new_rel_path)
        path = self.ws.abs_path(rel)
        if path.exists():
            raise ConcurrentModificationError(f"file appeared at target path: {rel}")
        # Verify the remote note still matches what we planned to write.
        current = self._fetch_remote_canonical(remote.id)
        if current is None:
            return OperationResult(op, "skipped", "remote note disappeared before apply")
        combined = note_hashes(
            current.title, current.body, current.tags, current.parent_id
        ).combined
        if combined != op.expected_remote_hash:
            raise ConcurrentModificationError(
                f"remote note changed between planning and apply: {remote.id}"
            )
        self._write_local_note(rel, remote.id, current.title, current.tags, current.body)
        self.store.upsert_note(
            note_id=remote.id, rel_path=rel, title=current.title, body=current.body,
            tags=current.tags, parent_id=current.parent_id, updated_time=current.updated_time,
        )
        self.store.remove_tombstone(remote.id)
        return OperationResult(op, "applied")

    def _op_pull_update_local(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.remote is not None and op.rel_path
        self._guard_local(op)
        remote = self._guard_remote(op)
        assert remote is not None
        old_rel = self.current_path(op.rel_path)
        new_rel = self.current_path(op.new_rel_path or op.rel_path)
        self.ws.backup_file(old_rel, self.run_id)
        self._write_local_note(new_rel, remote.id, remote.title, remote.tags, remote.body)
        if new_rel != old_rel:
            self.ws.abs_path(old_rel).unlink(missing_ok=True)
        self.store.upsert_note(
            note_id=remote.id, rel_path=new_rel, title=remote.title, body=remote.body,
            tags=remote.tags, parent_id=remote.parent_id, updated_time=remote.updated_time,
        )
        return OperationResult(op, "applied")

    def _op_pull_delete_local(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.base is not None and op.rel_path
        self._guard_local(op)
        rel = self.current_path(op.rel_path)
        # Confirm the remote note is really gone (trash or permanent).
        current = self.client.get_note(op.note_id or "", include_deleted=True)
        if current is not None and not current.get("deleted_time"):
            raise ConcurrentModificationError(
                f"remote note {op.note_id} reappeared; not deleting local file"
            )
        self.ws.backup_file(rel, self.run_id)
        quarantined = self.ws.quarantine_file(rel, self.run_id)
        self.store.add_tombstone(
            note_id=op.note_id or "", side="remote", rel_path=rel, title=state.base.title
        )
        self.store.delete_note(op.note_id or "")
        return OperationResult(op, "applied", f"local file quarantined to {quarantined}")

    def _op_push_create_folder(self, op: PlanOperation) -> OperationResult:
        fs = op.folder_state
        assert fs is not None and fs.local is not None and op.rel_path
        rel = self.current_path(op.rel_path)
        parent_rel = posixpath.dirname(rel)
        parent_id = "" if parent_rel == "" else self.resolve_folder_id(parent_rel)
        if parent_id is None:
            return OperationResult(op, "failed", f"parent notebook not resolved for {rel}")
        created = self.client.create_folder(title=fs.local.title, parent_id=parent_id)
        folder_id = created["id"]
        # Verify.
        check = self.client.get_folder(folder_id)
        if check is None or check.get("title") != fs.local.title:
            return OperationResult(op, "failed", "post-create verification failed")
        meta = {"id": folder_id, "parent_id": parent_id, "schema": 1, "title": fs.local.title}
        write_file_atomic(
            self.ws.abs_path(rel) / FOLDER_META,
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
        )
        self._created_folders[rel] = folder_id
        self.store.upsert_folder(
            folder_id=folder_id, rel_path=rel, title=fs.local.title, parent_id=parent_id
        )
        return OperationResult(op, "applied", f"created notebook {folder_id}")

    def _op_push_update_folder(self, op: PlanOperation) -> OperationResult:
        fs = op.folder_state
        assert fs is not None and fs.local is not None and fs.base is not None and op.folder_id
        current = self.client.get_folder(op.folder_id)
        if current is None:
            return OperationResult(op, "failed", "remote notebook disappeared")
        if (current.get("title"), current.get("parent_id", "")) != (fs.base.title, fs.base.parent_id):
            raise ConcurrentModificationError(
                f"remote notebook changed between planning and apply: {op.folder_id}"
            )
        fields: dict[str, Any] = {}
        if fs.local.title != fs.base.title:
            fields["title"] = fs.local.title
        if fs.local.parent_id != fs.base.parent_id:
            fields["parent_id"] = fs.local.parent_id
        if fields:
            self.client.update_folder(op.folder_id, fields)
        check = self.client.get_folder(op.folder_id)
        if check is None or (check.get("title"), check.get("parent_id", "")) != (
            fs.local.title,
            fs.local.parent_id,
        ):
            return OperationResult(op, "failed", "post-write verification failed")
        self.store.upsert_folder(
            folder_id=op.folder_id, rel_path=self.current_path(fs.local.rel_path),
            title=fs.local.title, parent_id=fs.local.parent_id,
        )
        return OperationResult(op, "applied")

    def _op_push_create_remote(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.local is not None and op.rel_path
        rel = self.current_path(op.rel_path)
        raw, parsed = self._read_local(rel)
        if parsed is not None and parsed.note_id is not None:
            raise ConcurrentModificationError(f"local file gained a note id before apply: {rel}")
        title = parsed.title if parsed else state.local.title
        body = parsed.body if parsed else canonicalize_body(raw)
        tags = parsed.tags if parsed else ()
        parent_rel = posixpath.dirname(rel)
        parent_id = self.resolve_folder_id(parent_rel)
        if parent_id is None:
            return OperationResult(op, "failed", f"parent notebook not resolved for {rel}")
        created = self.client.create_note(title=title, body=body, parent_id=parent_id)
        note_id = created["id"]
        if tags:
            self._reconcile_tags(note_id, tags)
        remote = self._fetch_remote_canonical(note_id)
        if remote is None:
            return OperationResult(op, "failed", "post-create verification failed")
        intended = note_hashes(title, body, tags, parent_id).combined
        actual = note_hashes(remote.title, remote.body, remote.tags, remote.parent_id).combined
        if intended != actual:
            return OperationResult(op, "failed", "post-create verification mismatch")
        # Write the returned id into the local metadata atomically and move
        # the file to its canonical name.
        new_rel = f"{parent_rel}/{note_filename(title, note_id)}"
        self._write_local_note(new_rel, note_id, title, tags, body)
        if new_rel != rel:
            self.ws.abs_path(rel).unlink(missing_ok=True)
        self.store.upsert_note(
            note_id=note_id, rel_path=new_rel, title=title, body=body, tags=tags,
            parent_id=parent_id, updated_time=remote.updated_time,
        )
        return OperationResult(op, "applied", f"created note {note_id}")

    def _op_push_update_remote(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.local is not None and op.note_id and op.rel_path
        self._guard_local(op)
        self._guard_remote(op)
        rel = self.current_path(op.rel_path)
        _, parsed = self._read_local(rel)
        assert parsed is not None  # guarded above
        parent_rel = posixpath.dirname(rel)
        parent_id = self.resolve_folder_id(parent_rel) or state.local.parent_id

        fields: dict[str, Any] = {}
        if "title" in op.fields:
            fields["title"] = parsed.title
        if "body" in op.fields:
            fields["body"] = parsed.body
        if "parent" in op.fields:
            fields["parent_id"] = parent_id
        if fields:
            try:
                self.client.update_note(op.note_id, fields)
            except AmbiguousWriteError:
                # Re-read and decide whether the write was applied.
                probe = self._fetch_remote_canonical(op.note_id)
                intended_probe = note_hashes(
                    parsed.title, parsed.body,
                    parsed.tags if "tags" in op.fields else (probe.tags if probe else ()),
                    parent_id if "parent" in op.fields else (probe.parent_id if probe else ""),
                ).combined
                applied = probe is not None and note_hashes(
                    probe.title, probe.body, probe.tags, probe.parent_id
                ).combined in (intended_probe,)
                if not applied and (
                    probe is None
                    or note_hashes(
                        probe.title, probe.body, probe.tags, probe.parent_id
                    ).combined != op.expected_remote_hash
                ):
                    raise ConcurrentModificationError(
                        f"ambiguous write and unexpected remote state for {op.note_id}"
                    ) from None
                if not applied:
                    return OperationResult(
                        op, "failed",
                        "write did not reach Joplin (timeout); state unchanged, rerun push",
                    )
        if "tags" in op.fields:
            self._reconcile_tags(op.note_id, parsed.tags)

        remote = self._fetch_remote_canonical(op.note_id)
        if remote is None:
            return OperationResult(op, "failed", "note disappeared during push")
        intended = note_hashes(parsed.title, parsed.body, parsed.tags, parent_id).combined
        actual = note_hashes(remote.title, remote.body, remote.tags, remote.parent_id).combined
        if intended != actual:
            raise ConcurrentModificationError(
                f"post-write verification failed for {op.note_id}: the note changed concurrently"
            )

        # Normalize the local filename if the title changed.
        new_rel = f"{parent_rel}/{note_filename(parsed.title, op.note_id)}"
        if new_rel != rel and not self.ws.abs_path(new_rel).exists():
            self._write_local_note(new_rel, op.note_id, parsed.title, parsed.tags, parsed.body)
            self.ws.abs_path(rel).unlink(missing_ok=True)
        else:
            new_rel = rel
        self.store.upsert_note(
            note_id=op.note_id, rel_path=new_rel, title=parsed.title, body=parsed.body,
            tags=parsed.tags, parent_id=parent_id, updated_time=remote.updated_time,
        )
        return OperationResult(op, "applied")

    def _op_push_delete_remote(self, op: PlanOperation) -> OperationResult:
        state = op.state
        assert state is not None and state.base is not None and op.note_id
        self._guard_remote(op)
        self.client.delete_note(op.note_id)  # normal trash, never permanent
        check = self.client.get_note(op.note_id, include_deleted=True)
        if check is not None and not check.get("deleted_time"):
            return OperationResult(op, "failed", "post-delete verification failed")
        self.store.add_tombstone(
            note_id=op.note_id, side="local", rel_path=state.base.rel_path,
            title=state.base.title,
        )
        self.store.delete_note(op.note_id)
        return OperationResult(op, "applied", "remote note moved to Joplin trash")

    def _op_rebase(self, op: PlanOperation) -> OperationResult:
        if op.folder_state is not None:
            fs = op.folder_state
            src = fs.local or fs.remote
            assert src is not None and fs.folder_id
            self.store.upsert_folder(
                folder_id=fs.folder_id,
                rel_path=self.current_path(fs.rel_path or ""),
                title=src.title, parent_id=src.parent_id,
            )
            return OperationResult(op, "applied")
        state = op.state
        assert state is not None and state.local is not None and state.remote is not None
        self._guard_local(op)
        remote = self._guard_remote(op)
        assert remote is not None
        local = state.local
        self.store.upsert_note(
            note_id=remote.id, rel_path=self.current_path(local.rel_path),
            title=local.title, body=local.body, tags=local.tags,
            parent_id=remote.parent_id, updated_time=remote.updated_time,
        )
        # A headerless or id-less file cannot occur here (id matched remote).
        return OperationResult(op, "applied", "base snapshot updated; both sides identical")

    def _op_drop_base(self, op: PlanOperation) -> OperationResult:
        if op.note_id:
            self.store.add_tombstone(
                note_id=op.note_id, side="both",
                rel_path=op.rel_path or "", title=(op.state.title if op.state else "") or "",
            )
            self.store.delete_note(op.note_id)
        elif op.folder_id:
            self.store.delete_folder(op.folder_id)
        return OperationResult(op, "applied")

    def _op_normalize_path(self, op: PlanOperation) -> OperationResult:
        assert op.rel_path and op.new_rel_path and op.note_id
        self._guard_local(op)
        old_rel = self.current_path(op.rel_path)
        new_rel = self.current_path(op.new_rel_path)
        if old_rel == new_rel:
            return OperationResult(op, "skipped", "already canonical")
        new_path = self.ws.abs_path(new_rel)
        if new_path.exists():
            return OperationResult(op, "skipped", f"target exists: {new_rel}")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.abs_path(old_rel).rename(new_path)
        base = self.store.get_note(op.note_id)
        if base is not None:
            self.store.upsert_note(
                note_id=base.id, rel_path=new_rel, title=base.title, body=base.body,
                tags=base.tags, parent_id=base.parent_id, updated_time=base.updated_time,
            )
        return OperationResult(op, "applied", f"renamed to {new_rel}")

    def _op_create_conflict(self, op: PlanOperation) -> OperationResult:
        from joplin_md_sync.conflicts import create_bundle  # local import: cycle avoidance

        state = op.state
        assert state is not None
        conflict_id = create_bundle(self.ws, self.store, state)
        return OperationResult(op, "applied", f"conflict bundle {conflict_id} created")

    # --- run loop -------------------------------------------------------

    def run(self, operations: list[PlanOperation]) -> ExecutionReport:
        report = ExecutionReport()
        for op in operations:
            try:
                result = self.execute(op)
            except ConcurrentModificationError as exc:
                result = OperationResult(op, "failed", f"CONCURRENT_MODIFICATION: {exc}")
                report.concurrent_failures += 1
            except AmbiguousWriteError as exc:
                result = OperationResult(
                    op, "failed", f"ambiguous write, manual check recommended: {exc}"
                )
            except Exception as exc:  # journal everything, keep going
                log.exception("operation %s failed", op.op_id)
                result = OperationResult(op, "failed", str(exc))
            report.results.append(result)
            if result.status == "applied":
                report.applied += 1
                if op.kind == models.OP_CREATE_CONFLICT:
                    report.conflicts_created += 1
            elif result.status == "skipped":
                report.skipped += 1
            else:
                report.failed += 1
            self.journal.mark(
                op.op_id,
                {"applied": OP_APPLIED, "skipped": OP_SKIPPED, "failed": OP_FAILED}[result.status],
                result.detail,
            )
        return report


def finalize_run(
    workspace: Workspace,
    store: StateStore,
    journal: Journal,
    snapshot: RemoteSnapshot,
    *,
    backup_retention: int = 10,
) -> None:
    """Post-run housekeeping: journal completion, cursor, backup pruning."""
    if snapshot.event_cursor:
        store.set_meta("event_cursor", snapshot.event_cursor)
    journal.finish(RUN_COMPLETE)
    workspace.prune_backups(backup_retention)
