"""Conflict bundles: creation, listing, resolution, discarding.

A bundle under ``.joplin-sync/conflicts/<conflict-id>/`` contains ``base.md``,
``local.md``, ``remote.md`` (each omitted when that side does not exist) and
``metadata.json``. Resolution revalidates both sides against the bundle and
refuses to apply a stale resolution.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from joplin_md_sync.api import JoplinClient
from joplin_md_sync.canonical import note_hashes
from joplin_md_sync.errors import (
    ConcurrentModificationError,
    JoplinSyncError,
    WorkspaceError,
)
from joplin_md_sync.metadata import (
    MetadataError,
    ParsedNoteFile,
    emit_note_file,
    parse_note_file,
)
from joplin_md_sync.models import ItemState
from joplin_md_sync.state import StateStore
from joplin_md_sync.sync import fetch_remote_canonical, reconcile_tags
from joplin_md_sync.workspace import Workspace, write_file_atomic

CATEGORY_DIVERGENT = "divergent_edit"
CATEGORY_NO_BASE = "no_base_divergent"
CATEGORY_DELETE_LOCAL = "delete_local_edit_remote"
CATEGORY_DELETE_REMOTE = "delete_remote_edit_local"


def _category(item: ItemState) -> str:
    if item.base is None:
        return CATEGORY_NO_BASE
    if item.local is None:
        return CATEGORY_DELETE_LOCAL
    if item.remote is None:
        return CATEGORY_DELETE_REMOTE
    return CATEGORY_DIVERGENT


def _combined(title: str, body: str, tags: tuple[str, ...], parent: str) -> str:
    return note_hashes(title, body, tags, parent).combined


def create_bundle(ws: Workspace, store: StateStore, item: ItemState) -> str:
    """Write a conflict bundle for a classified conflict; idempotent per note."""
    assert item.note_id is not None
    existing = store.open_conflict_for_note(item.note_id)
    if existing is not None:
        return str(existing["id"])

    conflict_id = uuid.uuid4().hex[:16]
    bundle_dir = ws.conflicts_dir / conflict_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "conflict_id": conflict_id,
        "note_id": item.note_id,
        "local_path": item.rel_path,
        "category": _category(item),
        "detected_time": _now_ms(),
        "local_deleted": item.local is None,
        "remote_deleted": item.remote is None,
    }
    if item.base is not None:
        write_file_atomic(
            bundle_dir / "base.md",
            emit_note_file(item.base.id, item.base.title, item.base.tags, item.base.body),
        )
        meta["base_hashes"] = _hashes_dict(
            item.base.title, item.base.body, item.base.tags, item.base.parent_id
        )
    if item.local is not None:
        write_file_atomic(
            bundle_dir / "local.md",
            emit_note_file(
                item.local.note_id, item.local.title, item.local.tags, item.local.body
            ),
        )
        meta["local_hashes"] = _hashes_dict(
            item.local.title, item.local.body, item.local.tags, item.local.parent_id
        )
    if item.remote is not None:
        write_file_atomic(
            bundle_dir / "remote.md",
            emit_note_file(item.remote.id, item.remote.title, item.remote.tags, item.remote.body),
        )
        meta["remote_hashes"] = _hashes_dict(
            item.remote.title, item.remote.body, item.remote.tags, item.remote.parent_id
        )
        meta["remote_updated_time"] = item.remote.updated_time

    write_file_atomic(bundle_dir / "metadata.json", json.dumps(meta, indent=2, sort_keys=True) + "\n")
    store.add_conflict(
        conflict_id=conflict_id,
        note_id=item.note_id,
        rel_path=item.rel_path or "",
        category=meta["category"],
    )
    return conflict_id


def _hashes_dict(title: str, body: str, tags: tuple[str, ...], parent: str) -> dict[str, str]:
    h = note_hashes(title, body, tags, parent)
    return {
        "title": h.title, "body": h.body, "tags": h.tags,
        "parent": h.parent, "combined": h.combined,
    }


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def load_bundle(ws: Workspace, conflict_id: str) -> dict[str, Any]:
    path = ws.conflicts_dir / conflict_id / "metadata.json"
    if not path.is_file():
        raise WorkspaceError(f"conflict bundle not found: {conflict_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_conflicts(store: StateStore) -> list[dict[str, Any]]:
    return [
        {
            "conflict_id": row["id"],
            "note_id": row["note_id"],
            "path": row["rel_path"],
            "category": row["category"],
            "created_time": row["created_time"],
        }
        for row in store.open_conflicts()
    ]


def show_conflict(ws: Workspace, store: StateStore, conflict_id: str) -> dict[str, Any]:
    row = store.get_conflict(conflict_id)
    if row is None:
        raise WorkspaceError(f"unknown conflict id: {conflict_id}")
    meta = load_bundle(ws, conflict_id)
    bundle_dir = ws.conflicts_dir / conflict_id
    sides: dict[str, str | None] = {}
    for side in ("base", "local", "remote"):
        p = bundle_dir / f"{side}.md"
        sides[side] = p.read_text(encoding="utf-8") if p.is_file() else None
    return {
        "conflict_id": conflict_id,
        "status": row["status"],
        "metadata": meta,
        "bundle_dir": str(bundle_dir),
        "sides": sides,
        "resolution_commands": [
            f"joplin-md-sync conflicts resolve {conflict_id} --take-local",
            f"joplin-md-sync conflicts resolve {conflict_id} --take-remote",
            f"joplin-md-sync conflicts resolve {conflict_id} --merged-file PATH",
        ],
    }


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _parse_bundle_side(ws: Workspace, conflict_id: str, side: str) -> ParsedNoteFile | None:
    p = ws.conflicts_dir / conflict_id / f"{side}.md"
    if not p.is_file():
        return None
    return parse_note_file(p.read_text(encoding="utf-8"))


def _current_local(ws: Workspace, rel_path: str) -> ParsedNoteFile | None:
    path = ws.abs_path(rel_path)
    if not path.is_file():
        return None
    return parse_note_file(path.read_text(encoding="utf-8"))


def _validate_not_stale(
    ws: Workspace, client: JoplinClient, meta: dict[str, Any], conflict_id: str
) -> None:
    """Both sides must still match the bundle; otherwise refuse to resolve."""
    note_id = meta["note_id"]
    rel_path = meta.get("local_path") or ""

    remote = fetch_remote_canonical(client, note_id)
    if meta.get("remote_deleted"):
        if remote is not None:
            raise ConcurrentModificationError(
                f"remote note {note_id} reappeared after the conflict was recorded; "
                "rerun sync to re-evaluate"
            )
    else:
        expected = (meta.get("remote_hashes") or {}).get("combined")
        actual = (
            _combined(remote.title, remote.body, remote.tags, remote.parent_id)
            if remote
            else None
        )
        if actual != expected:
            raise ConcurrentModificationError(
                f"remote note {note_id} changed after the conflict was recorded; "
                "rerun sync to create a fresh conflict"
            )

    local = _current_local(ws, rel_path) if rel_path else None
    if meta.get("local_deleted"):
        if local is not None:
            raise ConcurrentModificationError(
                f"local file {rel_path} reappeared after the conflict was recorded"
            )
    else:
        expected = (meta.get("local_hashes") or {}).get("combined")
        # Local parent hash was computed from the containing folder id; reuse
        # the bundle's recorded parent hash basis by comparing content parts.
        if local is None:
            raise ConcurrentModificationError(
                f"local file {rel_path} disappeared after the conflict was recorded"
            )
        lh = meta.get("local_hashes") or {}
        if (
            note_hashes(local.title, local.body, local.tags, "").title != lh.get("title")
            or note_hashes(local.title, local.body, local.tags, "").body != lh.get("body")
            or note_hashes(local.title, local.body, local.tags, "").tags != lh.get("tags")
        ):
            raise ConcurrentModificationError(
                f"local file {rel_path} changed after the conflict was recorded; "
                "rerun sync to create a fresh conflict"
            )


def resolve_conflict(
    ws: Workspace,
    store: StateStore,
    client: JoplinClient,
    conflict_id: str,
    *,
    mode: str,  # "take-local" | "take-remote" | "merged-file"
    merged_file: str | None = None,
    run_id: str = "resolve",
) -> dict[str, Any]:
    row = store.get_conflict(conflict_id)
    if row is None:
        raise WorkspaceError(f"unknown conflict id: {conflict_id}")
    if row["status"] != "open":
        raise WorkspaceError(f"conflict {conflict_id} is not open (status: {row['status']})")
    meta = load_bundle(ws, conflict_id)
    note_id: str = meta["note_id"]
    rel_path: str = meta.get("local_path") or ""

    _validate_not_stale(ws, client, meta, conflict_id)

    if mode == "take-local":
        result = _resolve_take_local(ws, store, client, meta, run_id)
    elif mode == "take-remote":
        result = _resolve_take_remote(ws, store, client, meta, run_id)
    elif mode == "merged-file":
        if not merged_file:
            raise JoplinSyncError("--merged-file PATH is required")
        result = _resolve_merged(ws, store, client, meta, merged_file, run_id)
    else:
        raise JoplinSyncError(f"unknown resolution mode: {mode}")

    # Resolution verified on both sides: close and remove the bundle.
    store.set_conflict_status(conflict_id, "resolved")
    shutil.rmtree(ws.conflicts_dir / conflict_id, ignore_errors=True)
    return {
        "conflict_id": conflict_id,
        "note_id": note_id,
        "path": result.get("path", rel_path),
        "resolution": mode,
        **result,
    }


def _push_content(
    ws: Workspace,
    store: StateStore,
    client: JoplinClient,
    note_id: str,
    rel_path: str,
    title: str,
    body: str,
    tags: tuple[str, ...],
    parent_id: str,
) -> dict[str, Any]:
    """PUT title/body(/parent) + tags, verify, write local file, update base."""
    remote = fetch_remote_canonical(client, note_id)
    if remote is None:
        # Remote side is gone: restore from trash when possible (real Joplin
        # restores via PUT deleted_time=0; POST with an existing id fails
        # with a UNIQUE-constraint error), otherwise recreate under the id.
        raw = client.get_note(note_id, include_deleted=True)
        if raw is not None and raw.get("deleted_time"):
            client.restore_note(note_id)
            client.update_note(note_id, {"title": title, "body": body, "parent_id": parent_id})
        else:
            created = client.create_note_with_id(
                note_id=note_id, title=title, body=body, parent_id=parent_id
            )
            if created.get("id") != note_id:
                raise JoplinSyncError(f"failed to recreate note {note_id}")
    else:
        fields: dict[str, Any] = {"title": title, "body": body}
        if parent_id and parent_id != remote.parent_id:
            fields["parent_id"] = parent_id
        client.update_note(note_id, fields)
    reconcile_tags(client, note_id, tags)
    check = fetch_remote_canonical(client, note_id)
    if check is None or _combined(check.title, check.body, check.tags, check.parent_id) != _combined(
        title, body, tags, check.parent_id
    ):
        raise ConcurrentModificationError(
            f"post-resolution verification failed for note {note_id}"
        )
    write_file_atomic(ws.abs_path(rel_path), emit_note_file(note_id, title, tags, body))
    store.upsert_note(
        note_id=note_id, rel_path=rel_path, title=title, body=body, tags=tags,
        parent_id=check.parent_id, updated_time=check.updated_time,
    )
    return {"remote_updated_time": check.updated_time, "path": rel_path}


def _resolve_take_local(
    ws: Workspace, store: StateStore, client: JoplinClient, meta: dict[str, Any], run_id: str
) -> dict[str, Any]:
    note_id = meta["note_id"]
    rel_path = meta.get("local_path") or ""
    if meta.get("local_deleted"):
        # Local side is the deletion: propagate it (trash the remote note).
        client.delete_note(note_id)
        check = client.get_note(note_id, include_deleted=True)
        if check is not None and not check.get("deleted_time"):
            raise ConcurrentModificationError(f"failed to trash remote note {note_id}")
        store.add_tombstone(note_id=note_id, side="local", rel_path=rel_path, title="")
        store.delete_note(note_id)
        return {"action": "remote note moved to Joplin trash"}
    local = _current_local(ws, rel_path)
    assert local is not None  # staleness check guarantees this
    base = store.get_note(note_id)
    parent_id = base.parent_id if base else ""
    result = _push_content(
        ws, store, client, note_id, rel_path, local.title, local.body, local.tags, parent_id
    )
    return {"action": "local content pushed to Joplin", **result}


def _resolve_take_remote(
    ws: Workspace, store: StateStore, client: JoplinClient, meta: dict[str, Any], run_id: str
) -> dict[str, Any]:
    note_id = meta["note_id"]
    rel_path = meta.get("local_path") or ""
    if meta.get("remote_deleted"):
        # Remote side is the deletion: quarantine the local file, drop base.
        if rel_path and ws.abs_path(rel_path).is_file():
            ws.backup_file(rel_path, run_id)
            quarantined = ws.quarantine_file(rel_path, run_id)
        else:
            quarantined = None
        store.add_tombstone(note_id=note_id, side="remote", rel_path=rel_path, title="")
        store.delete_note(note_id)
        return {"action": "local file quarantined; deletion accepted", "quarantine": quarantined}
    remote = fetch_remote_canonical(client, note_id)
    assert remote is not None  # staleness check guarantees this
    if not rel_path:
        base = store.get_note(note_id)
        if base is None:
            raise WorkspaceError(f"no local path known for note {note_id}")
        rel_path = base.rel_path
    if ws.abs_path(rel_path).is_file():
        ws.backup_file(rel_path, run_id)
    write_file_atomic(
        ws.abs_path(rel_path), emit_note_file(note_id, remote.title, remote.tags, remote.body)
    )
    store.upsert_note(
        note_id=note_id, rel_path=rel_path, title=remote.title, body=remote.body,
        tags=remote.tags, parent_id=remote.parent_id, updated_time=remote.updated_time,
    )
    return {"action": "remote content written to local file", "path": rel_path}


def _resolve_merged(
    ws: Workspace,
    store: StateStore,
    client: JoplinClient,
    meta: dict[str, Any],
    merged_file: str,
    run_id: str,
) -> dict[str, Any]:
    note_id = meta["note_id"]
    rel_path = meta.get("local_path") or ""
    merged_path = Path(merged_file)
    if not merged_path.is_file():
        raise WorkspaceError(f"merged file not found: {merged_file}")
    try:
        merged = parse_note_file(merged_path.read_text(encoding="utf-8"))
    except (MetadataError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"merged file is not a valid managed Markdown file: {exc}") from exc
    if merged.note_id is not None and merged.note_id != note_id:
        raise WorkspaceError(
            f"merged file id {merged.note_id} does not match conflict note {note_id}"
        )
    if not rel_path:
        base = store.get_note(note_id)
        rel_path = base.rel_path if base else ""
        if not rel_path:
            raise WorkspaceError(f"no local path known for note {note_id}")
    if ws.abs_path(rel_path).is_file():
        ws.backup_file(rel_path, run_id)
    base = store.get_note(note_id)
    parent_id = base.parent_id if base else ""
    result = _push_content(
        ws, store, client, note_id, rel_path,
        merged.title, merged.body, merged.tags, parent_id,
    )
    return {"action": "merged content applied to both sides", **result}


def discard_conflict(ws: Workspace, store: StateStore, conflict_id: str) -> dict[str, Any]:
    row = store.get_conflict(conflict_id)
    if row is None:
        raise WorkspaceError(f"unknown conflict id: {conflict_id}")
    store.set_conflict_status(conflict_id, "discarded")
    shutil.rmtree(ws.conflicts_dir / conflict_id, ignore_errors=True)
    return {
        "conflict_id": conflict_id,
        "note_id": row["note_id"],
        "action": "conflict bundle discarded; the next sync will re-detect the conflict "
        "if both sides still diverge",
    }
