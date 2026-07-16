# State Model and Synchronization Rules

For every note: `B` = base snapshot (last verified sync), `L` = current
local file, `R` = current Joplin note. Comparison uses canonical SHA-256
hashes of four components — title, body, sorted tag set, parent notebook —
plus a combined hash.

## Canonicalization

- line endings → `\n`; nothing else about the body changes (no trailing-
  whitespace stripping, no Markdown reformatting, no Unicode normalization);
- tags: lowercased, trimmed, deduplicated, sorted (matches Joplin's own
  tag-title normalization);
- hashing and file emission share the exact same canonicalization;
- component fields are length-prefixed before combined hashing, so field
  boundaries are unambiguous.

Remote change detection: if `R.updated_time == B.updated_time` the note's
title/body/parent are unchanged (Joplin bumps it on every note write) and
the body fetch is skipped. Tag changes do **not** bump `updated_time` and
are detected via the tag map. File mtimes are never used for correctness.

## Note state matrix

| B | L | R | Condition | Status | Planned action |
|---|---|---|-----------|--------|----------------|
| ✓ | ✓ | ✓ | L=B, R=B | `UNCHANGED` | none (path normalization at most) |
| ✓ | ✓ | ✓ | L≠B, R=B, body changed | `LOCAL_MODIFIED` | push update |
| ✓ | ✓ | ✓ | L≠B, R=B, title/tags only | `METADATA_MODIFIED` | push update |
| ✓ | ✓ | ✓ | L≠B, R=B, parent only | `MOVED_LOCAL` | push update |
| ✓ | ✓ | ✓ | L=B, R≠B | `REMOTE_MODIFIED` / `MOVED_REMOTE` | pull update |
| ✓ | ✓ | ✓ | L≠B, R≠B, L=R | `BOTH_IDENTICAL` | rebase (update B only) |
| ✓ | ✓ | ✓ | L≠B, R≠B, L≠R | `CONFLICT` | create bundle; touch nothing |
| ✓ | ✓ | ✗ | L=B | `REMOTE_DELETED` | report; quarantine only with flag |
| ✓ | ✓ | ✗ | L≠B | `DELETE_CONFLICT` | create bundle |
| ✓ | ✗ | ✓ | R=B | `LOCAL_DELETED` | report; trash only with flag |
| ✓ | ✗ | ✓ | R≠B | `DELETE_CONFLICT` | create bundle |
| ✓ | ✗ | ✗ | — | `BOTH_DELETED` | drop base |
| ✗ | ✓ | ✓ | L=R | `BOTH_IDENTICAL` | adopt base (reconstruction) |
| ✗ | ✓ | ✓ | L≠R | `CONFLICT` (no base) | create bundle |
| ✗ | ✓ | ✗ | id present | `INVALID_LOCAL_FILE` | blocked; remove id to recreate |
| ✗ | ✓ | ✗ | no id | `LOCAL_NEW` | push create (id written back atomically) |
| ✗ | ✗ | ✓ | — | `REMOTE_NEW` | pull create |

Joplin's own conflict notes are reported as `JOPLIN_CONFLICT_NOTE` and
never synchronized. Folder statuses (`FOLDER_*`) mirror the same idea;
folder deletions and folder conflicts are report-only in v1.

## Race protection (per operation)

Remote update: read note + tags → recompute canonical state → must equal
the planned remote state (else abort op, exit 5) → `PUT` only the intended
fields → reconcile tags separately → read again → must equal the intended
result → only then commit the base snapshot.

Local update: re-read and re-hash the file → must equal the planned local
state → write a temp file in the same directory → flush/fsync →
`os.replace()` → verify the resulting content → commit base.

One exclusive workspace lock (fcntl/msvcrt) guards every command; a second
process fails immediately with `WORKSPACE_LOCKED` (exit 5).

## Journal and recovery

Every mutating run persists its full plan before applying anything and
rewrites the journal atomically after each op. Ops are marked `applied`
only **after** the base commit; therefore an interrupted run is settled by
`recover` from current state alone: post-state verifiably present →
`applied`; otherwise → `skipped, rerun`. Incomplete journals block all
mutating commands (exit 6) until recovered.

## Deletion policy

Never propagated by default; always reported. With `--propagate-deletes`:
remote deletions quarantine the local file under
`.joplin-sync/quarantine/<run-id>/`; local deletions move the Joplin note
to the normal trash (never `permanent=1`). Delete-vs-edit is always a
conflict.
