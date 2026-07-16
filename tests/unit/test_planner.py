"""Exhaustive tests of the three-way state matrix and plan building."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import models
from joplin_md_sync.canonical import note_hashes
from joplin_md_sync.models import (
    BaseFolder,
    BaseNote,
    LocalFolderDir,
    LocalNoteFile,
    RemoteFolder,
    RemoteNote,
    RemoteSnapshot,
)
from joplin_md_sync.planner import build_plan, classify
from joplin_md_sync.workspace import LocalScan

FID = "f" * 32
NID = "a" * 32


def base_note(nid=NID, title="T", body="b\n", tags=("x",), parent=FID, path=None):
    return BaseNote(
        id=nid, rel_path=path or f"Work/T--{nid[:8]}.md", title=title, body=body,
        tags=tuple(tags), parent_id=parent, updated_time=1000,
        hashes=note_hashes(title, body, tags, parent),
    )


def local_note(nid=NID, title="T", body="b\n", tags=("x",), parent=FID, path=None, header=True):
    return LocalNoteFile(
        rel_path=path or f"Work/T--{nid[:8] if nid else 'new'}.md", note_id=nid,
        title=title, body=body, tags=tuple(tags), parent_id=parent, has_header=header,
    )


def remote_note(nid=NID, title="T", body="b\n", tags=("x",), parent=FID, updated=1000):
    return RemoteNote(
        id=nid, parent_id=parent, title=title, body=body, updated_time=updated,
        tags=tuple(tags),
    )


def make_scan(notes=(), folders=None):
    scan = LocalScan()
    scan.notes = list(notes)
    if folders is None:
        folders = [LocalFolderDir(rel_path="Work", folder_id=FID, title="Work", parent_id="")]
    for f in folders:
        if f.folder_id:
            scan.folders_by_id[f.folder_id] = f
        else:
            scan.candidate_folders.append(f)
        scan.folders_by_path[f.rel_path] = f
    return scan


def make_snapshot(notes=(), folders=None, trashed=(), conflicts=()):
    snap = RemoteSnapshot()
    for f in folders if folders is not None else [RemoteFolder(id=FID, parent_id="", title="Work")]:
        snap.folders[f.id] = f
    for n in notes:
        snap.notes[n.id] = n
    snap.trashed_note_ids = frozenset(trashed)
    snap.conflict_notes = tuple(conflicts)
    return snap


def one_status(classification, nid=NID):
    items = [i for i in classification.items if i.note_id == nid]
    assert len(items) == 1, items
    return items[0]


class StateMatrixTest(unittest.TestCase):
    def classify(self, base=None, local=None, remote=None, **kw):
        return classify(
            {base.id: base} if base else {},
            {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")},
            make_scan(notes=[local] if local else []),
            make_snapshot(notes=[remote] if remote else [], **kw),
        )

    def test_unchanged(self):
        c = self.classify(base=base_note(), local=local_note(), remote=remote_note())
        self.assertEqual(one_status(c).status, models.UNCHANGED)
        self.assertEqual(build_plan(c, direction="sync"), [])

    def test_local_only_body_change(self):
        c = self.classify(base=base_note(), local=local_note(body="edited\n"), remote=remote_note())
        item = one_status(c)
        self.assertEqual(item.status, models.LOCAL_MODIFIED)
        self.assertEqual(item.changed_components, ("body",))
        ops = build_plan(c, direction="push")
        self.assertEqual([op.kind for op in ops], [models.OP_PUSH_UPDATE_REMOTE])
        self.assertEqual(build_plan(c, direction="pull"), [])

    def test_local_metadata_only_change(self):
        c = self.classify(base=base_note(), local=local_note(tags=("x", "new")), remote=remote_note())
        self.assertEqual(one_status(c).status, models.METADATA_MODIFIED)

    def test_local_move_only(self):
        c = self.classify(
            base=base_note(), local=local_note(parent="e" * 32), remote=remote_note()
        )
        self.assertEqual(one_status(c).status, models.MOVED_LOCAL)

    def test_remote_only_change(self):
        c = self.classify(
            base=base_note(), local=local_note(),
            remote=remote_note(body="remote edit\n", updated=2000),
        )
        item = one_status(c)
        self.assertEqual(item.status, models.REMOTE_MODIFIED)
        ops = build_plan(c, direction="pull")
        self.assertEqual([op.kind for op in ops], [models.OP_PULL_UPDATE_LOCAL])
        self.assertEqual(build_plan(c, direction="push"), [])

    def test_remote_move_only(self):
        other = "e" * 32
        c = classify(
            {NID: base_note()},
            {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")},
            make_scan(notes=[local_note()]),
            make_snapshot(
                notes=[remote_note(parent=other, updated=2000)],
                folders=[
                    RemoteFolder(id=FID, parent_id="", title="Work"),
                    RemoteFolder(id=other, parent_id="", title="Other"),
                ],
            ),
        )
        self.assertEqual(one_status(c).status, models.MOVED_REMOTE)

    def test_same_change_both_sides(self):
        c = self.classify(
            base=base_note(),
            local=local_note(body="same edit\n"),
            remote=remote_note(body="same edit\n", updated=2000),
        )
        item = one_status(c)
        self.assertEqual(item.status, models.BOTH_IDENTICAL)
        ops = build_plan(c, direction="sync")
        self.assertEqual([op.kind for op in ops], [models.OP_REBASE])

    def test_divergent_change_is_conflict(self):
        c = self.classify(
            base=base_note(),
            local=local_note(body="local edit\n"),
            remote=remote_note(body="remote edit\n", updated=2000),
        )
        self.assertEqual(one_status(c).status, models.CONFLICT)
        ops = build_plan(c, direction="sync")
        self.assertEqual([op.kind for op in ops], [models.OP_CREATE_CONFLICT])
        # An existing open bundle suppresses duplicate conflict creation.
        c2 = classify(
            {NID: base_note()},
            {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")},
            make_scan(notes=[local_note(body="local edit\n")]),
            make_snapshot(notes=[remote_note(body="remote edit\n", updated=2000)]),
            open_conflict_note_ids=frozenset({NID}),
        )
        self.assertEqual(build_plan(c2, direction="sync"), [])

    def test_local_new_note(self):
        c = self.classify(local=local_note(nid=None, path="Work/new.md"))
        items = [i for i in c.items if i.status == models.LOCAL_NEW]
        self.assertEqual(len(items), 1)
        ops = build_plan(c, direction="push")
        self.assertEqual([op.kind for op in ops], [models.OP_PUSH_CREATE_REMOTE])
        self.assertEqual(build_plan(c, direction="pull"), [])

    def test_remote_new_note(self):
        c = self.classify(remote=remote_note())
        self.assertEqual(one_status(c).status, models.REMOTE_NEW)
        ops = build_plan(c, direction="pull")
        self.assertEqual([op.kind for op in ops], [models.OP_PULL_CREATE_LOCAL])
        self.assertEqual(ops[0].new_rel_path, f"Work/T--{NID[:8]}.md")

    # --- deletions ---------------------------------------------------------

    def test_local_deleted_remote_unchanged(self):
        c = self.classify(base=base_note(), remote=remote_note())
        self.assertEqual(one_status(c).status, models.LOCAL_DELETED)
        self.assertEqual(build_plan(c, direction="push"), [])  # conservative default
        ops = build_plan(c, direction="push", propagate_deletes=True)
        self.assertEqual([op.kind for op in ops], [models.OP_PUSH_DELETE_REMOTE])

    def test_remote_deleted_local_unchanged(self):
        c = self.classify(base=base_note(), local=local_note(), trashed={NID})
        self.assertEqual(one_status(c).status, models.REMOTE_DELETED)
        self.assertEqual(build_plan(c, direction="pull"), [])
        ops = build_plan(c, direction="pull", propagate_deletes=True)
        self.assertEqual([op.kind for op in ops], [models.OP_PULL_DELETE_LOCAL])

    def test_local_deleted_remote_changed(self):
        c = self.classify(base=base_note(), remote=remote_note(body="edited\n", updated=2000))
        self.assertEqual(one_status(c).status, models.DELETE_CONFLICT)
        ops = build_plan(c, direction="sync")
        self.assertEqual([op.kind for op in ops], [models.OP_CREATE_CONFLICT])

    def test_remote_deleted_local_changed(self):
        c = self.classify(base=base_note(), local=local_note(body="edited\n"), trashed={NID})
        self.assertEqual(one_status(c).status, models.DELETE_CONFLICT)

    def test_both_deleted(self):
        c = self.classify(base=base_note())
        self.assertEqual(one_status(c).status, models.BOTH_DELETED)
        ops = build_plan(c, direction="sync")
        self.assertEqual([op.kind for op in ops], [models.OP_DROP_BASE])

    # --- no-base reconstruction ---------------------------------------------

    def test_no_base_identical_adopts(self):
        c = self.classify(local=local_note(), remote=remote_note())
        item = one_status(c)
        self.assertEqual(item.status, models.BOTH_IDENTICAL)
        ops = build_plan(c, direction="sync")
        self.assertEqual([op.kind for op in ops], [models.OP_ADOPT_BASE])

    def test_no_base_divergent_is_conflict(self):
        c = self.classify(local=local_note(body="a\n"), remote=remote_note(body="b\n"))
        self.assertEqual(one_status(c).status, models.CONFLICT)

    def test_local_with_unknown_id_is_invalid(self):
        c = self.classify(local=local_note())
        self.assertEqual(one_status(c).status, models.INVALID_LOCAL_FILE)
        self.assertEqual(build_plan(c, direction="sync"), [])

    def test_duplicate_local_ids_invalid(self):
        c = classify(
            {}, {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")},
            make_scan(notes=[local_note(path="Work/a.md"), local_note(path="Work/b.md")]),
            make_snapshot(notes=[remote_note()]),
        )
        self.assertTrue(any("duplicate note id" in inv.reason for inv in c.invalid))

    def test_joplin_conflict_note_reported(self):
        c = self.classify(conflicts=((NID, "Conflicted"),))
        item = one_status(c)
        self.assertEqual(item.status, models.JOPLIN_CONFLICT_NOTE)
        self.assertEqual(build_plan(c, direction="sync"), [])


class FolderMatrixTest(unittest.TestCase):
    def test_remote_new_folder(self):
        c = classify(
            {}, {}, make_scan(folders=[]),
            make_snapshot(folders=[RemoteFolder(id=FID, parent_id="", title="Work")]),
        )
        f = [x for x in c.folder_items if x.status == models.FOLDER_REMOTE_NEW]
        self.assertEqual(len(f), 1)
        ops = build_plan(c, direction="pull")
        self.assertEqual([op.kind for op in ops], [models.OP_PULL_CREATE_DIR])

    def test_local_new_folder(self):
        c = classify(
            {}, {},
            make_scan(folders=[LocalFolderDir(rel_path="Fresh", folder_id=None, title="Fresh", parent_id="")]),
            make_snapshot(folders=[]),
        )
        ops = build_plan(c, direction="push")
        self.assertEqual([op.kind for op in ops], [models.OP_PUSH_CREATE_FOLDER])

    def test_folder_conflict_reported_not_planned(self):
        base = {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")}
        c = classify(
            {}, base,
            make_scan(folders=[LocalFolderDir(rel_path="Work", folder_id=FID, title="LocalName", parent_id="")]),
            make_snapshot(folders=[RemoteFolder(id=FID, parent_id="", title="RemoteName")]),
        )
        f = [x for x in c.folder_items if x.status == models.FOLDER_CONFLICT]
        self.assertEqual(len(f), 1)
        self.assertEqual(build_plan(c, direction="sync"), [])

    def test_nested_remote_paths(self):
        child = "c" * 32
        c = classify(
            {}, {}, make_scan(folders=[]),
            make_snapshot(folders=[
                RemoteFolder(id=FID, parent_id="", title="Parent"),
                RemoteFolder(id=child, parent_id=FID, title="Child"),
            ]),
        )
        self.assertEqual(c.remote_folder_paths[child], "Parent/Child")
        ops = build_plan(c, direction="pull")
        self.assertEqual([op.new_rel_path for op in ops], ["Parent", "Parent/Child"])

    def test_sibling_title_collision_disambiguated(self):
        other = "d" * 32
        c = classify(
            {}, {}, make_scan(folders=[]),
            make_snapshot(folders=[
                RemoteFolder(id=FID, parent_id="", title="Same"),
                RemoteFolder(id=other, parent_id="", title="same"),
            ]),
        )
        paths = set(c.remote_folder_paths.values())
        self.assertEqual(len(paths), 2)


class PlanDeterminismTest(unittest.TestCase):
    def test_plan_is_deterministic(self):
        def build():
            c = classify(
                {NID: base_note()},
                {FID: BaseFolder(id=FID, rel_path="Work", title="Work", parent_id="")},
                make_scan(notes=[local_note(body="x\n")]),
                make_snapshot(notes=[remote_note()]),
            )
            return [(op.op_id, op.kind, op.rel_path) for op in build_plan(c, direction="sync")]

        self.assertEqual(build(), build())


if __name__ == "__main__":
    unittest.main()
