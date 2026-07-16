# CLI Reference

Global conventions:

- `--json`: deterministic machine-readable envelope on **stdout**; logs and
  diagnostics go to **stderr**. No ANSI codes in JSON. Keys are sorted.
- `--verbose` / `--quiet` control stderr logging; `--log-file PATH` adds a
  debug log file. The token is redacted from all channels.
- Connection options (where relevant): `--base-url URL`, `--port N`,
  `--token-file PATH`, `--timeout SECONDS`, `--allow-remote-api`.
  Without any of them (and without env/workspace overrides) the built-in
  default `http://127.0.0.1:41184` is used when it answers `/ping`, with a
  fallback scan of ports 41184-41194. Only the token must be configured.
- Workspace commands take `--root PATH` (default: current directory).

## JSON envelope

Every `--json` response contains:

```json
{
  "schema_version": 1,
  "command": "push",
  "success": true,
  "exit_code": 0,
  "code": "OK",
  "tool_version": "1.0.0",
  "workspace": "/abs/path/notes"
}
```

plus command-specific fields (`summary`, `items`, `execution`,
`planned_operations`, `checks`, `conflicts`, ...). `success` is `true` for
exit codes 0, 1, 2, 8 (the command completed; the code describes the
outcome) and `false` for operational errors (3–7, 9).

## Exit codes

See the table in [AGENTS.md](../AGENTS.md#exit-codes-stable); it is part of
the public contract. Priority when several apply to one run:
`5` (concurrent) > `6` (partial failure) > `2` (conflicts) > `0`.

## Commands

### `version`, `capabilities`, `update-check`

- `version --json` → tool, python, protocol/state/output schema versions,
  distribution kind (`wheel` / `zipapp` / `source`).
- `capabilities --json` → command list, feature flags, exit-code table.
- `update-check --json [--include-prerelease] [--offline]` → queries the
  GitHub Releases API. Exit 0 current, 8 outdated
  (`code: VERSION_OUTDATED`, with an exact `update_command`), 4 when the
  check cannot be completed (`code: UPDATE_CHECK_FAILED`), 0 with
  `code: UPDATE_CHECK_SKIPPED` for `--offline`. It never self-updates.

### `init --root PATH [--mode remote-first|local-first]`

Creates `.joplin-sync/` (state DB, config, journal/backup/conflict dirs)
and a `.gitignore`. `remote-first` (default) refuses to run when unmanaged
`*.md` files already exist (exit 7, listing them). `local-first` adopts
them: files without a Joplin id are pushed as new notes, and the first real
`push` requires a preceding `push --dry-run`.

### `doctor --root PATH [--offline]`

Checks python version, workspace integrity, state DB, incomplete runs,
open conflicts, invalid files, lock availability, Joplin ping + token.
Exit code = severity of the first problem (3/6/2/4/5), 0 when healthy.

### `status --root PATH`

Offline: local files vs base snapshot only (`remote_state: "unknown"`).
Reports tracked counts, local changes, open conflicts, incomplete runs.

### `pull` / `push` / `sync --root PATH [--dry-run] [--propagate-deletes]`

Scan → classify → plan → journal → apply (guard, apply, verify, commit
base per operation). `--dry-run` prints `planned_operations` and exits
1 when work is pending (0 when clean, 2 when conflicts exist), mutating
nothing. Deletions are only reported unless `--propagate-deletes`.

### `diff --root PATH [modes]`

Never mutates. Modes: `--summary` (default), `--name-status`, `--unified`,
`--three-way`, `--against remote|base`, `--note ID_OR_PATH`, `--offline`.
Exit code is always 0 unless `--exit-code` is passed (then 1 = differences,
2 = conflicts). Unified diff labels: `joplin/<note-id>`,
`local/<relative-path>`, `base/<note-id>`.

### `recover --root PATH`

Settles incomplete journals: verifies which operations completed (base
snapshots are committed before an op is marked applied, so current state is
the witness), marks the rest "not applied; rerun", removes stray temp
files, and unblocks the workspace.

### `conflicts list | show ID | resolve ID MODE | discard ID`

See [CONFLICTS.md](CONFLICTS.md).

### `note set-title PATH TITLE`, `note set-tags PATH [TAG...]`, `note validate PATH`

Safe header editing (atomic rewrite; adds a header to plain files) and
validation (exit 3 for a malformed header).

### `resources pull --root PATH`

Downloads every `:/resource-id` referenced by managed notes into
`.joplin-sync/resources/<id>[.ext]`. Markdown links are never rewritten.
