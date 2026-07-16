# Conflict Handling

A conflict is created whenever both sides changed the same note since the
base snapshot and the changes differ (including delete-vs-edit and
no-base divergence after state loss). The tool never picks a side itself.

## Bundle layout

`.joplin-sync/conflicts/<conflict-id>/`:

| File | Content |
| --- | --- |
| `base.md` | note at the last successful sync (absent when no base) |
| `local.md` | local side at detection time (absent when locally deleted) |
| `remote.md` | remote side at detection time (absent when remotely deleted) |
| `metadata.json` | conflict id, note id, path, category, detected time, per-side hashes, remote `updated_time` |

Categories: `divergent_edit`, `no_base_divergent`,
`delete_local_edit_remote`, `delete_remote_edit_local`.

## Commands

```bash
joplin-md-sync conflicts list --root PATH --json     # exit 2 when any open
joplin-md-sync conflicts show CONFLICT_ID --root PATH --json
joplin-md-sync conflicts resolve CONFLICT_ID --take-local
joplin-md-sync conflicts resolve CONFLICT_ID --take-remote
joplin-md-sync conflicts resolve CONFLICT_ID --merged-file PATH
joplin-md-sync conflicts discard CONFLICT_ID
```

## Resolution semantics

Before applying anything, both sides are re-read and compared with the
hashes stored in the bundle. If either side changed again, resolution is
refused with exit 5 — rerun `sync` to get a fresh conflict.

- `--take-local`: pushes the bundled local content to Joplin (verifies
  afterwards). For `delete_local_edit_remote` it trashes the remote note;
  for `delete_remote_edit_local` it recreates the note in Joplin under the
  same id.
- `--take-remote`: writes the remote content to the local file. For
  deletions it quarantines the local file / restores it respectively.
- `--merged-file PATH`: the file must be valid managed Markdown whose id
  (if present) matches the conflict; the merged content is applied to both
  sides and verified on both.
- `discard`: removes the bundle without touching either side; the next
  sync re-detects the conflict while the divergence persists.

After a verified resolution the bundle directory is removed and the base
snapshot is updated. Automatic textual merging is intentionally not
performed; only an explicit `--merged-file` merges content.
