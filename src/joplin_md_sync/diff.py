"""Diff rendering: summary, name-status, unified, three-way, JSON.

``diff`` never mutates anything. Offline mode substitutes the base snapshot
for the remote side, so remote state is explicitly reported as unknown.
"""

from __future__ import annotations

import difflib
from typing import Any

from joplin_md_sync import models
from joplin_md_sync.models import (
    BaseFolder,
    BaseNote,
    ItemState,
    RemoteFolder,
    RemoteNote,
    RemoteSnapshot,
)
from joplin_md_sync.planner import Classification


def snapshot_from_base(
    base_notes: dict[str, BaseNote], base_folders: dict[str, BaseFolder]
) -> RemoteSnapshot:
    """Fake remote snapshot equal to the base: offline local-vs-base diffing."""
    snapshot = RemoteSnapshot()
    for folder in base_folders.values():
        snapshot.folders[folder.id] = RemoteFolder(
            id=folder.id, parent_id=folder.parent_id, title=folder.title
        )
    for note in base_notes.values():
        snapshot.notes[note.id] = RemoteNote(
            id=note.id, parent_id=note.parent_id, title=note.title, body=note.body,
            updated_time=note.updated_time, tags=note.tags,
        )
    return snapshot


def summary_counts(classification: Classification) -> dict[str, Any]:
    by_status = classification.summary()
    return {
        "unchanged": by_status.get(models.UNCHANGED, 0),
        "local_modified": (
            by_status.get(models.LOCAL_MODIFIED, 0)
            + by_status.get(models.METADATA_MODIFIED, 0)
            + by_status.get(models.MOVED_LOCAL, 0)
        ),
        "remote_modified": (
            by_status.get(models.REMOTE_MODIFIED, 0) + by_status.get(models.MOVED_REMOTE, 0)
        ),
        "local_new": by_status.get(models.LOCAL_NEW, 0),
        "remote_new": by_status.get(models.REMOTE_NEW, 0),
        "local_deleted": by_status.get(models.LOCAL_DELETED, 0),
        "remote_deleted": by_status.get(models.REMOTE_DELETED, 0),
        "conflicts": (
            by_status.get(models.CONFLICT, 0) + by_status.get(models.DELETE_CONFLICT, 0)
        ),
        "invalid": by_status.get(models.INVALID_LOCAL_FILE, 0),
        "by_status": by_status,
    }


def name_status_lines(classification: Classification) -> list[str]:
    lines: list[str] = []
    for item in classification.items:
        if item.status == models.UNCHANGED:
            continue
        ref = item.rel_path or item.note_id or "?"
        lines.append(f"{item.status}\t{ref}")
    for fitem in classification.folder_items:
        ref = fitem.rel_path or fitem.folder_id or "?"
        lines.append(f"{fitem.status}\t{ref}/")
    for invalid in classification.invalid:
        lines.append(f"{models.INVALID_LOCAL_FILE}\t{invalid.rel_path}\t{invalid.reason}")
    return lines


def _split_keepends(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _unified(a_text: str, b_text: str, a_label: str, b_label: str) -> str:
    lines = difflib.unified_diff(
        _split_keepends(a_text), _split_keepends(b_text),
        fromfile=a_label, tofile=b_label, lineterm="\n",
    )
    return "".join(lines)


def _meta_lines(side: str, item: ItemState) -> list[str]:
    """Human-readable non-body component changes."""
    out: list[str] = []
    components = (
        item.changed_components if side == "local" else item.remote_changed_components
    )
    obj = item.local if side == "local" else item.remote
    base = item.base
    if obj is None or base is None:
        return out
    if "title" in components:
        out.append(f"  {side} title: {base.title!r} -> {obj.title!r}")
    if "tags" in components:
        out.append(f"  {side} tags: {sorted(base.tags)!r} -> {sorted(obj.tags)!r}")
    if "parent" in components:
        out.append(f"  {side} notebook: {base.parent_id} -> {obj.parent_id}")
    return out


def unified_output(
    classification: Classification, *, against: str = "remote", three_way: bool = False
) -> str:
    """Deterministic unified diffs for every changed item."""
    blocks: list[str] = []
    for item in classification.items:
        if item.status in (models.UNCHANGED, models.JOPLIN_CONFLICT_NOTE):
            continue
        nid = item.note_id or "new"
        base_label = f"base/{nid}"
        local_label = f"local/{item.rel_path or nid}"
        remote_label = f"joplin/{nid}"
        base_body = item.base.body if item.base else ""
        local_body = item.local.body if item.local else ""
        remote_body = item.remote.body if item.remote else ""

        header = f"=== {item.status} {item.rel_path or nid}"
        chunk: list[str] = [header]
        chunk.extend(_meta_lines("local", item))
        chunk.extend(_meta_lines("remote", item))

        if three_way:
            if item.local is not None and (item.base or item.local):
                d = _unified(base_body, local_body, base_label, local_label)
                if d:
                    chunk.append(d.rstrip("\n"))
            if item.remote is not None:
                d = _unified(base_body, remote_body, base_label, remote_label)
                if d:
                    chunk.append(d.rstrip("\n"))
        elif against == "base":
            d = _unified(base_body, local_body, base_label, local_label)
            if d:
                chunk.append(d.rstrip("\n"))
        else:  # against remote
            d = _unified(remote_body, local_body, remote_label, local_label)
            if d:
                chunk.append(d.rstrip("\n"))

        if len(chunk) > 1:
            blocks.append("\n".join(chunk))
        else:
            blocks.append(header + f"  ({item.detail or 'no body change'})")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def items_json(classification: Classification, *, remote_known: bool = True) -> list[dict[str, Any]]:
    items = [item.to_json() for item in classification.items]
    items.extend(fitem.to_json() for fitem in classification.folder_items)
    items.extend(
        {"status": models.INVALID_LOCAL_FILE, "path": inv.rel_path, "detail": inv.reason}
        for inv in classification.invalid
    )
    if not remote_known:
        for item in items:
            item["remote_state"] = "unknown"
    return items


def filter_note(classification: Classification, ref: str) -> Classification:
    """Keep only the item matching a note id or a relative path."""
    filtered = Classification(
        invalid=[i for i in classification.invalid if i.rel_path == ref],
        remote_folder_paths=classification.remote_folder_paths,
    )
    filtered.items = [
        i for i in classification.items if ref in (i.note_id, i.rel_path)
    ]
    filtered.folder_items = [
        f for f in classification.folder_items if ref in (f.folder_id, f.rel_path)
    ]
    return filtered
