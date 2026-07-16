"""Three-way classification (base / local / remote) and operation planning.

This module is pure: it never touches the network or the filesystem, which
makes the state matrix exhaustively unit-testable. ``classify()`` produces
``ItemState``/``FolderState`` records; ``build_plan()`` turns them into an
ordered, immutable list of ``PlanOperation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from joplin_md_sync import models
from joplin_md_sync.canonical import changed_components, note_hashes
from joplin_md_sync.models import (
    BaseFolder,
    BaseNote,
    FolderState,
    ItemState,
    LocalNoteFile,
    PlanOperation,
    RemoteSnapshot,
)
from joplin_md_sync.paths import folder_dirname, note_filename
from joplin_md_sync.workspace import LocalScan


@dataclass
class Classification:
    items: list[ItemState] = field(default_factory=list)
    folder_items: list[FolderState] = field(default_factory=list)
    invalid: list[models.InvalidLocalFile] = field(default_factory=list)
    # Canonical target paths computed from the remote tree:
    remote_folder_paths: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        for fitem in self.folder_items:
            counts[fitem.status] = counts.get(fitem.status, 0) + 1
        if self.invalid:
            counts[models.INVALID_LOCAL_FILE] = (
                counts.get(models.INVALID_LOCAL_FILE, 0) + len(self.invalid)
            )
        return dict(sorted(counts.items()))


def _remote_folder_paths(snapshot: RemoteSnapshot) -> dict[str, str]:
    """Deterministic canonical directory path for every remote folder."""
    children: dict[str, list[models.RemoteFolder]] = {}
    for folder in snapshot.folders.values():
        parent = folder.parent_id if folder.parent_id in snapshot.folders else ""
        children.setdefault(parent, []).append(folder)

    paths: dict[str, str] = {}

    def walk(parent_id: str, prefix: str, seen: frozenset[str]) -> None:
        taken: set[str] = set()
        for folder in sorted(children.get(parent_id, []), key=lambda f: (f.title, f.id)):
            if folder.id in seen:  # defensive: parent cycle in remote data
                continue
            name = folder_dirname(folder.title, folder.id, taken)
            taken.add(name.casefold())
            path = f"{prefix}{name}"
            paths[folder.id] = path
            walk(folder.id, path + "/", seen | {folder.id})

    walk("", "", frozenset())
    return paths


def _note_target_path(note: models.RemoteNote, folder_paths: dict[str, str]) -> str | None:
    folder_path = folder_paths.get(note.parent_id)
    if folder_path is None:
        return None  # parent folder unknown (should not happen for active notes)
    return f"{folder_path}/{note_filename(note.title, note.id)}"


def classify(
    base_notes: dict[str, BaseNote],
    base_folders: dict[str, BaseFolder],
    scan: LocalScan,
    snapshot: RemoteSnapshot,
    *,
    open_conflict_note_ids: frozenset[str] = frozenset(),
) -> Classification:
    result = Classification(invalid=list(scan.invalid))
    result.remote_folder_paths = _remote_folder_paths(snapshot)

    _classify_folders(base_folders, scan, snapshot, result)
    _classify_notes(base_notes, scan, snapshot, result, open_conflict_note_ids)

    for conflict_id, title in snapshot.conflict_notes:
        result.items.append(
            ItemState(
                status=models.JOPLIN_CONFLICT_NOTE,
                note_id=conflict_id,
                rel_path=None,
                title=title,
                detail="conflict note created by Joplin's own sync; resolve it in Joplin",
            )
        )

    result.items.sort(key=lambda i: (i.rel_path or "", i.note_id or ""))
    result.folder_items.sort(key=lambda f: (f.rel_path or "", f.folder_id or ""))
    return result


def _classify_folders(
    base_folders: dict[str, BaseFolder],
    scan: LocalScan,
    snapshot: RemoteSnapshot,
    result: Classification,
) -> None:
    ids = set(base_folders) | set(scan.folders_by_id) | set(snapshot.folders)
    for fid in sorted(ids):
        base = base_folders.get(fid)
        local = scan.folders_by_id.get(fid)
        remote = snapshot.folders.get(fid)

        if base and local and remote:
            l_changed = (local.title, local.parent_id) != (base.title, base.parent_id)
            r_changed = (remote.title, remote.parent_id) != (base.title, base.parent_id)
            if not l_changed and not r_changed:
                continue  # unchanged folders are not reported
            if l_changed and r_changed:
                if (local.title, local.parent_id) == (remote.title, remote.parent_id):
                    result.folder_items.append(
                        FolderState(
                            status=models.BOTH_IDENTICAL, folder_id=fid, rel_path=local.rel_path,
                            title=local.title, detail="same folder change on both sides",
                            base=base, local=local, remote=remote,
                        )
                    )
                else:
                    result.folder_items.append(
                        FolderState(
                            status=models.FOLDER_CONFLICT, folder_id=fid, rel_path=local.rel_path,
                            title=local.title,
                            detail="folder renamed/moved differently on both sides; resolve manually",
                            base=base, local=local, remote=remote,
                        )
                    )
            elif l_changed:
                result.folder_items.append(
                    FolderState(
                        status=models.FOLDER_LOCAL_MODIFIED, folder_id=fid,
                        rel_path=local.rel_path, title=local.title,
                        base=base, local=local, remote=remote,
                    )
                )
            else:
                result.folder_items.append(
                    FolderState(
                        status=models.FOLDER_REMOTE_MODIFIED, folder_id=fid,
                        rel_path=local.rel_path, title=remote.title,
                        base=base, local=local, remote=remote,
                    )
                )
        elif base and local and not remote:
            result.folder_items.append(
                FolderState(
                    status=models.FOLDER_REMOTE_DELETED, folder_id=fid,
                    rel_path=local.rel_path, title=local.title,
                    detail="notebook deleted in Joplin; local directory kept (folder deletions never propagate in v1)",
                    base=base, local=local,
                )
            )
        elif base and remote and not local:
            result.folder_items.append(
                FolderState(
                    status=models.FOLDER_LOCAL_DELETED, folder_id=fid,
                    rel_path=base.rel_path, title=remote.title,
                    detail="local notebook directory deleted; remote notebook kept (folder deletions never propagate in v1)",
                    base=base, remote=remote,
                )
            )
        elif local and remote and not base:
            # Reconstruction after a lost/cloned state database.
            result.folder_items.append(
                FolderState(
                    status=models.BOTH_IDENTICAL, folder_id=fid, rel_path=local.rel_path,
                    title=local.title, detail="folder base snapshot adopted",
                    local=local, remote=remote,
                )
            )
        elif local and not remote and not base:
            result.invalid.append(
                models.InvalidLocalFile(
                    rel_path=local.rel_path,
                    reason=f"folder id {fid} does not exist in Joplin; "
                    "remove .joplin-folder.json to treat it as a new notebook",
                )
            )
        elif remote and not local and not base:
            result.folder_items.append(
                FolderState(
                    status=models.FOLDER_REMOTE_NEW, folder_id=fid,
                    rel_path=result.remote_folder_paths.get(fid), title=remote.title,
                    remote=remote,
                )
            )
        elif base and not local and not remote:
            result.folder_items.append(
                FolderState(
                    status=models.FOLDER_LOCAL_DELETED, folder_id=fid, rel_path=base.rel_path,
                    title=base.title, detail="folder gone on both sides; base forgotten",
                    base=base,
                )
            )

    # Local directories without .joplin-folder.json are new notebook candidates.
    for cand in scan.candidate_folders:
        result.folder_items.append(
            FolderState(
                status=models.FOLDER_LOCAL_NEW, folder_id=None,
                rel_path=cand.rel_path, title=cand.title, local=cand,
            )
        )


def _classify_notes(
    base_notes: dict[str, BaseNote],
    scan: LocalScan,
    snapshot: RemoteSnapshot,
    result: Classification,
    open_conflict_note_ids: frozenset[str],
) -> None:
    local_by_id: dict[str, LocalNoteFile] = {}
    new_locals: list[LocalNoteFile] = []
    for note in scan.notes:
        if note.note_id is None:
            new_locals.append(note)
        elif note.note_id in local_by_id:
            result.invalid.append(
                models.InvalidLocalFile(
                    rel_path=note.rel_path,
                    reason=f"duplicate note id {note.note_id} "
                    f"(also at {local_by_id[note.note_id].rel_path})",
                )
            )
        else:
            local_by_id[note.note_id] = note

    ids = set(base_notes) | set(local_by_id) | set(snapshot.notes)
    for nid in sorted(ids):
        base = base_notes.get(nid)
        local = local_by_id.get(nid)
        remote = snapshot.notes.get(nid)
        item = _classify_one_note(nid, base, local, remote, snapshot, open_conflict_note_ids)
        if item is not None:
            result.items.append(item)

    for note in new_locals:
        result.items.append(
            ItemState(
                status=models.LOCAL_NEW, note_id=None, rel_path=note.rel_path,
                title=note.title, local=note,
                detail="" if note.has_header else "no metadata header; will be adopted on push",
            )
        )


def _classify_one_note(
    nid: str,
    base: BaseNote | None,
    local: LocalNoteFile | None,
    remote: models.RemoteNote | None,
    snapshot: RemoteSnapshot,
    open_conflict_note_ids: frozenset[str],
) -> ItemState | None:
    l_hashes = (
        note_hashes(local.title, local.body, local.tags, local.parent_id) if local else None
    )
    r_hashes = (
        note_hashes(remote.title, remote.body, remote.tags, remote.parent_id) if remote else None
    )

    if base and local and remote:
        assert l_hashes and r_hashes
        local_diff = changed_components(l_hashes, base.hashes)
        remote_diff = changed_components(r_hashes, base.hashes)
        if not local_diff and not remote_diff:
            return ItemState(
                status=models.UNCHANGED, note_id=nid, rel_path=local.rel_path,
                title=local.title, base=base, local=local, remote=remote,
            )
        if local_diff and not remote_diff:
            return ItemState(
                status=_local_status(local_diff), note_id=nid, rel_path=local.rel_path,
                title=local.title, changed_components=local_diff,
                base=base, local=local, remote=remote,
            )
        if remote_diff and not local_diff:
            status = models.MOVED_REMOTE if remote_diff == ("parent",) else models.REMOTE_MODIFIED
            return ItemState(
                status=status, note_id=nid, rel_path=local.rel_path, title=remote.title,
                remote_changed_components=remote_diff, base=base, local=local, remote=remote,
            )
        if l_hashes.combined == r_hashes.combined:
            return ItemState(
                status=models.BOTH_IDENTICAL, note_id=nid, rel_path=local.rel_path,
                title=local.title, changed_components=local_diff,
                remote_changed_components=remote_diff,
                detail="same change on both sides", base=base, local=local, remote=remote,
            )
        return ItemState(
            status=models.CONFLICT, note_id=nid, rel_path=local.rel_path, title=local.title,
            changed_components=local_diff, remote_changed_components=remote_diff,
            detail=_conflict_detail(nid, open_conflict_note_ids),
            base=base, local=local, remote=remote,
        )

    if base and local and not remote:
        assert l_hashes
        where = "trash" if nid in snapshot.trashed_note_ids else "Joplin"
        if l_hashes.combined == base.hashes.combined:
            return ItemState(
                status=models.REMOTE_DELETED, note_id=nid, rel_path=local.rel_path,
                title=local.title, detail=f"note deleted in {where}; local file kept "
                "(pass --propagate-deletes to quarantine it)",
                base=base, local=local,
            )
        return ItemState(
            status=models.DELETE_CONFLICT, note_id=nid, rel_path=local.rel_path,
            title=local.title, changed_components=changed_components(l_hashes, base.hashes),
            detail=f"edited locally but deleted in {where}",
            base=base, local=local,
        )

    if base and remote and not local:
        assert r_hashes
        if r_hashes.combined == base.hashes.combined:
            return ItemState(
                status=models.LOCAL_DELETED, note_id=nid, rel_path=base.rel_path,
                title=base.title, detail="local file deleted; remote note kept "
                "(pass --propagate-deletes on push to move it to Joplin trash)",
                base=base, remote=remote,
            )
        return ItemState(
            status=models.DELETE_CONFLICT, note_id=nid, rel_path=base.rel_path,
            title=remote.title,
            remote_changed_components=changed_components(r_hashes, base.hashes),
            detail="deleted locally but edited in Joplin",
            base=base, remote=remote,
        )

    if base and not local and not remote:
        return ItemState(
            status=models.BOTH_DELETED, note_id=nid, rel_path=base.rel_path, title=base.title,
            detail="deleted on both sides; base snapshot will be dropped", base=base,
        )

    if local and remote and not base:
        assert l_hashes and r_hashes
        if l_hashes.combined == r_hashes.combined:
            return ItemState(
                status=models.BOTH_IDENTICAL, note_id=nid, rel_path=local.rel_path,
                title=local.title, detail="base snapshot adopted (reconstructed workspace)",
                local=local, remote=remote,
            )
        return ItemState(
            status=models.CONFLICT, note_id=nid, rel_path=local.rel_path, title=local.title,
            detail="local and remote differ and no base snapshot exists "
            "(reconstructed workspace); " + _conflict_detail(nid, open_conflict_note_ids),
            local=local, remote=remote,
        )

    if local and not remote and not base:
        where = (
            "exists only in Joplin trash"
            if nid in snapshot.trashed_note_ids
            else "does not exist in Joplin"
        )
        return ItemState(
            status=models.INVALID_LOCAL_FILE, note_id=nid, rel_path=local.rel_path,
            title=local.title,
            detail=f"note id {where} and no base snapshot exists; "
            "remove the 'id' key from the header to push it as a new note",
            local=local,
        )

    if remote and not local and not base:
        return ItemState(
            status=models.REMOTE_NEW, note_id=nid, rel_path=None, title=remote.title,
            remote=remote,
        )

    return None


def _local_status(local_diff: tuple[str, ...]) -> str:
    if local_diff == ("parent",):
        return models.MOVED_LOCAL
    if "body" not in local_diff:
        return models.METADATA_MODIFIED
    return models.LOCAL_MODIFIED


def _conflict_detail(nid: str, open_conflict_note_ids: frozenset[str]) -> str:
    if nid in open_conflict_note_ids:
        return "conflict bundle already exists; see 'conflicts list'"
    return "divergent concurrent change; a conflict bundle will be created by the next mutating command"


# --------------------------------------------------------------------------
# Plan building
# --------------------------------------------------------------------------

_PULL_NOTE_STATUSES = {models.REMOTE_MODIFIED, models.MOVED_REMOTE}
_PUSH_NOTE_STATUSES = {models.LOCAL_MODIFIED, models.METADATA_MODIFIED, models.MOVED_LOCAL}


def build_plan(
    classification: Classification,
    *,
    direction: str,  # "pull" | "push" | "sync"
    propagate_deletes: bool = False,
) -> list[PlanOperation]:
    """Turn a classification into a deterministic, ordered operation list."""
    ops: list[PlanOperation] = []
    seq = 0

    def new_op(kind: str, **kwargs: object) -> PlanOperation:
        nonlocal seq
        seq += 1
        return PlanOperation(op_id=f"op-{seq:04d}", kind=kind, **kwargs)  # type: ignore[arg-type]

    pull = direction in ("pull", "sync")
    push = direction in ("push", "sync")
    folder_paths = classification.remote_folder_paths

    # 1. Folder creations (remote -> local), parents before children.
    if pull:
        for fs in sorted(
            (f for f in classification.folder_items if f.status == models.FOLDER_REMOTE_NEW),
            key=lambda f: f.rel_path or "",
        ):
            assert fs.remote is not None
            ops.append(
                new_op(
                    models.OP_PULL_CREATE_DIR, folder_id=fs.folder_id,
                    new_rel_path=fs.rel_path, folder_state=fs, detail=f"notebook '{fs.title}'",
                )
            )
        for fs in (f for f in classification.folder_items if f.status == models.FOLDER_REMOTE_MODIFIED):
            assert fs.local is not None and fs.remote is not None
            ops.append(
                new_op(
                    models.OP_PULL_UPDATE_DIR, folder_id=fs.folder_id,
                    rel_path=fs.local.rel_path, new_rel_path=folder_paths.get(fs.folder_id or ""),
                    folder_state=fs, detail=f"notebook renamed/moved to '{fs.remote.title}'",
                )
            )

    # 2. Folder creations (local -> remote), parents before children (path order).
    if push:
        for fs in sorted(
            (f for f in classification.folder_items if f.status == models.FOLDER_LOCAL_NEW),
            key=lambda f: f.rel_path or "",
        ):
            ops.append(
                new_op(
                    models.OP_PUSH_CREATE_FOLDER, rel_path=fs.rel_path,
                    folder_state=fs, detail=f"new notebook '{fs.title}'",
                )
            )
        for fs in (f for f in classification.folder_items if f.status == models.FOLDER_LOCAL_MODIFIED):
            assert fs.local is not None
            ops.append(
                new_op(
                    models.OP_PUSH_UPDATE_FOLDER, folder_id=fs.folder_id,
                    rel_path=fs.local.rel_path, folder_state=fs,
                    detail=f"notebook renamed/moved to '{fs.local.title}'",
                )
            )

    # Folder base adoption/rebase (both directions do the same).
    for fs in (f for f in classification.folder_items if f.status == models.BOTH_IDENTICAL):
        ops.append(
            new_op(
                models.OP_ADOPT_BASE, folder_id=fs.folder_id,
                rel_path=fs.rel_path, folder_state=fs, detail=fs.detail,
            )
        )
    for fs in (
        f
        for f in classification.folder_items
        if f.status == models.FOLDER_LOCAL_DELETED and f.remote is None and f.base is not None
    ):
        ops.append(
            new_op(models.OP_DROP_BASE, folder_id=fs.folder_id, rel_path=fs.rel_path,
                   folder_state=fs, detail="folder gone on both sides")
        )

    # 3. Note operations.
    for item in classification.items:
        status = item.status
        if status in (models.UNCHANGED,):
            # Cosmetic path normalization on pull only.
            if pull and item.remote is not None and item.local is not None:
                target = _note_target_path(item.remote, folder_paths)
                if target is not None and target != item.local.rel_path:
                    ops.append(
                        new_op(
                            models.OP_NORMALIZE_LOCAL_PATH, note_id=item.note_id,
                            rel_path=item.local.rel_path, new_rel_path=target, state=item,
                            expected_local_hash=_combined(item.local),
                            detail="rename to canonical filename",
                        )
                    )
            continue

        if status in _PULL_NOTE_STATUSES and pull:
            assert item.remote is not None and item.local is not None
            ops.append(
                new_op(
                    models.OP_PULL_UPDATE_LOCAL, note_id=item.note_id,
                    rel_path=item.local.rel_path,
                    new_rel_path=_note_target_path(item.remote, folder_paths) or item.local.rel_path,
                    state=item,
                    expected_local_hash=_combined(item.local),
                    expected_remote_hash=_combined(item.remote),
                    detail=f"remote changed: {', '.join(sorted(item.remote_changed_components))}",
                )
            )
        elif status == models.REMOTE_NEW and pull:
            assert item.remote is not None
            target = _note_target_path(item.remote, folder_paths)
            if target is None:
                continue  # parent folder not visible; skip safely
            ops.append(
                new_op(
                    models.OP_PULL_CREATE_LOCAL, note_id=item.note_id, new_rel_path=target,
                    state=item, expected_remote_hash=_combined(item.remote),
                    detail=f"new remote note '{item.title}'",
                )
            )
        elif status == models.REMOTE_DELETED and pull and propagate_deletes:
            assert item.local is not None
            ops.append(
                new_op(
                    models.OP_PULL_DELETE_LOCAL, note_id=item.note_id,
                    rel_path=item.local.rel_path, state=item,
                    expected_local_hash=_combined(item.local),
                    detail="remote note deleted; local file will be quarantined",
                )
            )
        elif status in _PUSH_NOTE_STATUSES and push:
            assert item.local is not None
            ops.append(
                new_op(
                    models.OP_PUSH_UPDATE_REMOTE, note_id=item.note_id,
                    rel_path=item.local.rel_path,
                    fields=item.changed_components, state=item,
                    expected_local_hash=_combined(item.local),
                    expected_remote_hash=_combined(item.remote) if item.remote else None,
                    detail=f"local changed: {', '.join(sorted(item.changed_components))}",
                )
            )
        elif status == models.LOCAL_NEW and push:
            assert item.local is not None
            ops.append(
                new_op(
                    models.OP_PUSH_CREATE_REMOTE, rel_path=item.local.rel_path, state=item,
                    expected_local_hash=_combined(item.local),
                    detail=f"new local note '{item.title}'",
                )
            )
        elif status == models.LOCAL_DELETED and push and propagate_deletes:
            ops.append(
                new_op(
                    models.OP_PUSH_DELETE_REMOTE, note_id=item.note_id, rel_path=item.rel_path,
                    state=item,
                    expected_remote_hash=_combined(item.remote) if item.remote else None,
                    detail="local file deleted; remote note will move to Joplin trash",
                )
            )
        elif status == models.BOTH_IDENTICAL:
            kind = models.OP_ADOPT_BASE if item.base is None else models.OP_REBASE
            ops.append(
                new_op(
                    kind, note_id=item.note_id, rel_path=item.rel_path, state=item,
                    expected_local_hash=_combined(item.local) if item.local else None,
                    expected_remote_hash=_combined(item.remote) if item.remote else None,
                    detail=item.detail,
                )
            )
        elif status == models.BOTH_DELETED:
            ops.append(
                new_op(models.OP_DROP_BASE, note_id=item.note_id, rel_path=item.rel_path,
                       state=item, detail=item.detail)
            )
        elif status in (models.CONFLICT, models.DELETE_CONFLICT):
            if "bundle already exists" not in item.detail:
                ops.append(
                    new_op(
                        models.OP_CREATE_CONFLICT, note_id=item.note_id, rel_path=item.rel_path,
                        state=item,
                        expected_local_hash=_combined(item.local) if item.local else None,
                        expected_remote_hash=_combined(item.remote) if item.remote else None,
                        detail=item.detail,
                    )
                )
        # LOCAL_DELETED/REMOTE_DELETED without --propagate-deletes,
        # INVALID_LOCAL_FILE, JOPLIN_CONFLICT_NOTE, FOLDER_* deletions and
        # FOLDER_CONFLICT are report-only: no operation is planned.

    return ops


def _combined(obj: LocalNoteFile | models.RemoteNote | None) -> str | None:
    if obj is None:
        return None
    return note_hashes(obj.title, obj.body, obj.tags, obj.parent_id).combined
