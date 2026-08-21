# Conflict Handling

This is the operator procedure for conflicts. Detection, evidence, and
freshness requirements are owned by
[`SYN-004`](../contracts/SYNCHRONIZATION.md#syn-004-divergence-creates-evidence-without-overwriting)
and
[`SYN-005`](../contracts/SYNCHRONIZATION.md#syn-005-conflict-resolution-is-explicit-and-freshness-checked).

## Bundle layout

`.joplin-sync/conflicts/<conflict-id>/`:

| File | Use during review |
| --- | --- |
| `base.md` | note at the last successful sync (absent when no base) |
| `local.md` | local side at detection time (absent when locally deleted) |
| `remote.md` | remote side at detection time (absent when remotely deleted) |
| `metadata.json` | identity, category, timestamps, and freshness evidence |

## Commands

```bash
joplin-md-sync conflicts list --root PATH --json     # exit 2 when any open
joplin-md-sync conflicts show CONFLICT_ID --root PATH --json
joplin-md-sync conflicts resolve CONFLICT_ID --take-local
joplin-md-sync conflicts resolve CONFLICT_ID --take-remote
joplin-md-sync conflicts resolve CONFLICT_ID --merged-file PATH
joplin-md-sync conflicts discard CONFLICT_ID
```

## Choose a resolution

- Use `--take-local` when the local representation is the intended result.
- Use `--take-remote` when Joplin is the intended result.
- Use `--merged-file PATH` after manually producing a valid combined managed
  file.
- Use `discard` only to remove the recorded bundle without choosing a result.

If resolution reports concurrent modification, run `sync` again and review the
new evidence. Do not edit or delete bundle files by hand.
