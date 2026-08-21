# CLI Reference

This page is a user reference for commands and options. The stable machine
interface is defined only by the [CLI and output contract](../contracts/CLI.md);
synchronization behavior is defined by the
[synchronization contract](../contracts/SYNCHRONIZATION.md).

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
- `--help` prints the parser reference; top-level `--version` prints the package
  version without the richer `version` command envelope.

## JSON envelope

For example, a successful `push --json` response begins with the common fields
owned by [`CLI-001`](../contracts/CLI.md#cli-001-json-output-is-deterministic-and-isolated-from-diagnostics):

```json
{
  "schema_version": 1,
  "command": "push",
  "success": true,
  "exit_code": 0,
  "code": "OK",
  "tool_version": "1.5.5",
  "workspace": "/abs/path/notes"
}
```

Command-specific fields include `summary`, `items`, `execution`,
`planned_operations`, `checks`, and `conflicts`. Read `exit_code` and `code`
together instead of parsing human-readable diagnostics.

## Exit codes

The complete stable mapping and evidence are in
[`CLI-002`](../contracts/CLI.md#cli-002-exit-codes-and-capabilities-remain-stable).
Clients can discover the same public surface with `capabilities --json`.

## Commands

### `version`, `capabilities`, `update-check`

- `version --json` → tool, python, protocol/state/output schema versions,
  distribution kind (`standalone` / `wheel` / `zipapp` / `source`).
- `capabilities --json` → command list, feature flags, exit-code table.
- `update-check --json [--include-prerelease] [--offline]` → queries the
  GitHub Releases API. Exit 0 current, 8 outdated
  (`code: VERSION_OUTDATED`, with an exact `update_command`), 4 when the
  check cannot be completed (`code: UPDATE_CHECK_FAILED`), 0 with
  `code: UPDATE_CHECK_SKIPPED` for `--offline`. It never self-updates.
  Use `--include-prerelease` only when preview releases are relevant.

### `init --root PATH [--mode remote-first|local-first]`

Creates `.joplin-sync/` (state DB, config, journal/backup/conflict dirs)
and a `.gitignore`. `remote-first` (default) refuses to run when unmanaged
`*.md` files already exist (exit 7, listing them). `local-first` adopts
them: files without a Joplin id are pushed as new notes, and the first real
`push` requires a preceding `push --dry-run`.
`--mode` selects the initialization policy.

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

### `mcp serve [connection and server options]`

Runs a foreground MCP Streamable HTTP server at
`http://127.0.0.1:8765/mcp` by default. It exposes note, notebook, tag, and
binary-resource CRUD, trash/restore where Joplin supports it, Markdown/HTML
content, attachments, relationship traversal, and full-text search. No
workspace is required.

For note creation, pass an existing `parent_id` or a `notebook_title` to
find/create a root notebook. With neither, `MCP Notes` is found or created.
Explicit create tools reject an occupied natural identity; see
[Create conflicts](MCP_API.md#create-conflicts) for the comparison keys and
recovery workflow.

Joplin connection options are the normal `--base-url`, `--port`,
`--token-file`, `--timeout`, and `--allow-remote-api`. Server options:

- `--host HOST`, `--mcp-port PORT`, `--mcp-path PATH`
- `--retry-timeout SECONDS`, `--retry-delay SECONDS`,
  `--discovery-timeout SECONDS`
- `--auth-token-file PATH` to enable MCP pre-shared bearer authorization; the
  protected file must contain a URL-safe Base64 token encoding at least 32
  bytes generated by a cryptographically secure random generator
- repeatable `--allowed-origin ORIGIN`
- `--allow-remote-mcp` for an explicit non-loopback bind; this also requires
  `--auth-token-file`
- `--gpt-actions` to add authenticated `/api/gpt/v1/tools/*` routes to the
  same listener
- `--gpt-actions-token-file PATH` (required with `--gpt-actions`)
- `--gpt-actions-max-request-bytes N`,
  `--gpt-actions-max-response-chars N`,
  `--gpt-actions-max-concurrency N`, and
  `--gpt-actions-rate-limit REQUESTS_PER_MINUTE`

The command intentionally stays in the foreground. Full protocol behavior,
security guidance, MCP client configuration, and service-manager examples are
split by purpose between [MCP API](MCP_API.md) and
[Joplin API Service](SERVICE.md).

Actions-only options are rejected unless `--gpt-actions` is present. The
Actions token file is required, re-read on each request, protected by strict
POSIX permissions, and must differ from the Joplin and MCP credentials.

### `gpt-actions export-openapi --server-url URL --output PATH`

Generates deterministic OpenAPI 3.1 JSON from the shared tool registry without
contacting Joplin. `--server-url` must receive an HTTPS origin on port 443 with
no path, query, fragment, or credentials. `--output` selects the destination
JSON file. The result reports `operation_count`, `registry_hash`, `server_url`,
and `output`.

The repository does not store a generated contract. Export it with the real
public hostname immediately before importing or updating a Custom GPT. See
[ChatGPT Actions end-to-end setup](CHATGPT_ACTIONS.md).
