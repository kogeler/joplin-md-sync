# Architecture

## Module map

```
cli.py          argparse tree, JSON envelope, exit-code mapping, logging
config.py       connection resolution: CLI > env > workspace > discovery
api.py          Joplin Data API client (urllib): pagination, GET retries,
                ambiguous-write surfacing, token redaction, port discovery
mcp_service.py  validated note/notebook/tag/resource operations, relationship
                traversal, base64 limits, bounded Joplin availability waits
mcp_server.py   MCP JSON-RPC dispatcher, tool registry, Streamable HTTP,
                Origin checks and optional bearer authorization
canonical.py    body/tag canonicalization + SHA-256 component hashing
metadata.py     managed Markdown header parse/emit (single-line JSON comment)
paths.py        cross-platform filename sanitization, traversal guards
workspace.py    layout, recursive scan, atomic writes, backups, quarantine
state.py        SQLite: base snapshots, tombstones, conflicts, run records,
                integrity check, migration framework
locking.py      exclusive cross-platform lock (fcntl / msvcrt)
journal.py      crash-safe run journal + recovery
planner.py      PURE three-way classification and plan building
sync.py         remote snapshot + executor (guard/apply/verify/commit)
diff.py         summary / name-status / unified / three-way rendering
conflicts.py    bundles, staleness-checked resolution
resources.py    resource download
update_check.py GitHub releases freshness check
errors.py       exception hierarchy = exit codes + result codes
models.py       shared dataclasses and status constants
```

Separation rule: transport (api), state (state/workspace), decision
(planner — pure, no I/O), execution (sync), rendering (cli/diff) never
blur. The planner is exhaustively unit-testable because it takes plain
data structures.

The MCP path is deliberately separate from the workspace sync engine:

```
MCP client -> mcp_server (HTTP + JSON-RPC) -> mcp_service -> api -> Joplin
```

It directly manages Joplin notes, notebooks, tags, and resources and does not
mutate workspace state. The service layer is transport-independent, so future
transports can reuse its validation, metadata shaping, relationship handling,
multipart uploads, and outage behavior.

## Data flow of a mutating command

```
lock workspace (exclusive)
check for incomplete journals            -> exit 6 if any
scan local files                         (workspace.py)
snapshot remote                          (sync.build_remote_snapshot)
classify base/local/remote               (planner.classify — pure)
build ordered operation plan             (planner.build_plan — pure)
[--dry-run stops here]
persist plan to journal                  (journal.begin)
for each op:  guard -> apply -> verify -> commit base -> journal.mark
finalize: cursor, journal complete, prune backups
```

## Key design decisions

1. **Zero runtime dependencies.** Everything is stdlib; tests use
   `unittest`; the fake Joplin server is `http.server`. This keeps the
   wheel/pyz trivially portable and the supply-chain surface nil.
2. **Base snapshots are full copies**, not hashes only. A true three-way
   comparison and offline `status`/`diff --offline` need base content, and
   it lets `build_remote_snapshot` skip body fetches for notes whose
   `updated_time` is unchanged.
3. **Tags are reconciled via the tag map** (`GET /tags` +
   `GET /tags/:id/notes`), because tag attachment does not bump a note's
   `updated_time`. Cost scales with number of tags, not notes.
4. **Optimistic concurrency, no fake locks.** The Joplin API has no
   conditional writes; instead every op re-reads its inputs immediately
   before applying and re-reads the result after, aborting that single op
   (exit 5) on drift. Joplin revisions are never used as versions/ETags.
5. **The journal marks an op applied only after the base commit**, so
   recovery can decide "applied or not" purely from current state, without
   re-running network operations.
6. **Ambiguous writes** (timeout after a PUT) are settled by re-reading:
   result == intended → applied; result == pre-state → reported as failed
   (safe rerun); anything else → concurrent modification.
7. **`/events` is not used in v1** (full reconciliation is cheap at the
   target scale and events cover only notes anyway); the cursor is stored
   for a future optimization.
8. **Filenames are cosmetic.** Identity lives in the header id + state DB;
   pull normalizes names; a rename alone is never a content change.

## State database (schema v1)

```
meta          key/value: state_schema_version, event_cursor, ...
notes         id, rel_path, title, body, tags(json), parent_id,
              updated_time, component hashes, combined hash
folders       id, rel_path, title, parent_id
tombstones    note_id, side (local/remote/both), rel_path, title, time
conflicts     id, note_id, rel_path, category, created_time, status
journal_runs  run_id, command, started_time, status, journal_path
```

Migrations: `state.MIGRATIONS[from_version]` chains upward; opening a DB
with a *newer* schema than the tool supports fails with a clear message.
