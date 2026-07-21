# joplin-md-sync — Agent Runbook

Safe two-way sync between a local Joplin desktop app and a directory of
Markdown files. Built for autonomous coding agents: deterministic JSON
output, stable exit codes, no silent overwrites.

## Requirements

- CPython **>= 3.13** (tested on 3.13 and 3.14), Windows or Linux, when using
  source/wheel/zipapp distributions. Native release executables include Python.
- Joplin desktop running locally with the **Web Clipper service** enabled
  (Tools > Options > Web Clipper). Default port `41184`.
- The Joplin **token** (shown in the Web Clipper options page).

## Install

```bash
python -m pip install "git+https://github.com/kogeler/joplin-md-sync.git@v1.4.0"
# or with pipx:
pipx install "git+https://github.com/kogeler/joplin-md-sync.git@v1.4.0"
# or run the standalone zipapp from a GitHub release asset:
python joplin-md-sync.pyz --help
# or run a native release executable without installing Python:
./joplin-md-sync-linux-amd64 version
```

Verify the install and check freshness:

```bash
joplin-md-sync version --json
joplin-md-sync update-check --json    # exit 8 = newer release exists
```

## Working from a checkout (development)

Everything is driven by the Makefile (CI runs the same targets):

```bash
make venv        # runtime venv/ with the CLI installed:  venv/bin/joplin-md-sync
make venv-dev    # tooling venv-dev/ from the requirements-dev.txt lock
make check       # lint (ruff) + typecheck (mypy) + full test suite
make test-live   # opt-in live MCP + GPT Actions tests; reads ./token; not CI
make package     # wheel, sdist, pyz, current-platform executable, checksums
make smoke       # install the built wheel into a clean venv and exercise it
make help        # list all targets
```

- The version's **single source is the `.version` file at the repo root**
  (pyproject reads it dynamically; `agent-manifest.json` must match — both
  are enforced by `make verify-release`).
- Runtime dependencies are declared in `pyproject.toml` (`dependencies`,
  currently empty by design); dev tools are declared in
  `[dependency-groups]` and pinned via pip freeze in `requirements-dev.txt`
  (refresh with `make freeze`).

## Authentication

| Source (highest wins) | How |
| --- | --- |
| CLI | `--base-url URL`, `--port N`, `--token-file PATH` |
| Environment | `JOPLIN_TOKEN`, `JOPLIN_BASE_URL`, `JOPLIN_PORT` |
| Built-in default | `http://127.0.0.1:41184` (standard Clipper endpoint) |
| Discovery fallback | probes `127.0.0.1:41184-41194` via `GET /ping` |

**Only `JOPLIN_TOKEN` is required** when Joplin runs with default Clipper
settings — the URL and port never need to be specified. The token is never
accepted as a raw CLI argument, never logged, never stored in the
workspace. Non-loopback API addresses are refused unless
`--allow-remote-api` is passed.

## Canonical workflow

```bash
export JOPLIN_TOKEN=...                                   # once per session
joplin-md-sync version --json
joplin-md-sync update-check --json
joplin-md-sync init --root ./notes --mode remote-first    # first time only
joplin-md-sync doctor --root ./notes --json
joplin-md-sync pull --root ./notes --json
# ... edit Markdown files with normal filesystem tools ...
joplin-md-sync diff --root ./notes --three-way --unified
joplin-md-sync push --root ./notes --dry-run --json
joplin-md-sync push --root ./notes --json
joplin-md-sync status --root ./notes --json
```

When unsure about state:

```bash
joplin-md-sync diff --root ./notes --three-way --json
joplin-md-sync conflicts list --root ./notes --json
```

## Managed file format

Every managed note starts with one single-line header, then one blank
line, then the exact Joplin Markdown body:

```markdown
<!-- joplin-md-sync: {"id":"17a35454fbb34ee080e29fba9ee88730","schema":1,"tags":["homelab"],"title":"Kubernetes"} -->

The exact Joplin Markdown body begins here.
```

- **Keep the header intact.** A malformed header blocks push for that file.
- To create a new note: add a `.md` file inside a notebook directory,
  either with a header without `"id"` or as plain Markdown (the file name
  becomes the title). Push assigns the Joplin id and rewrites the header.
- Edit `title`/`tags` via the header or, safer:
  `joplin-md-sync note set-title PATH "New title"` /
  `note set-tags PATH tag1 tag2` / `note validate PATH`.
- `:/resource-id` links must stay unchanged;
  `joplin-md-sync resources pull --root ./notes` downloads the binaries to
  `.joplin-sync/resources/` for inspection.
- Notebook = directory with `.joplin-folder.json`. A new directory becomes
  a new notebook on push. Directory names are cosmetic (normalized on
  pull); identity lives in the metadata files.

## Command table

| Command | Purpose | Mutates |
| --- | --- | --- |
| `version` / `capabilities` / `update-check` | environment info | no |
| `init --root P [--mode remote-first\|local-first]` | create workspace | local |
| `doctor --root P [--offline]` | health checks | no |
| `status --root P` | offline state vs base | no |
| `diff --root P [--three-way --unified --name-status --note X --exit-code --offline]` | compare states | **never** |
| `pull --root P [--dry-run]` | remote -> local | local |
| `push --root P [--dry-run]` | local -> remote | remote |
| `sync --root P [--dry-run]` | both directions | both |
| `recover --root P` | settle interrupted runs | local state |
| `conflicts list/show/resolve/discard` | conflict handling | varies |
| `note set-title/set-tags/validate` | header editing | local file |
| `resources pull --root P` | download attachments | `.joplin-sync/` only |
| `mcp serve [--host H --mcp-port N]` | combined MCP/Actions Joplin API daemon | notes, notebooks, tags, resources |
| `gpt-actions export-openapi --server-url U --output P` | generate Custom GPT Actions contract | output file |

All operational commands accept `--json`, `--verbose`, `--quiet`,
`--log-file PATH`. JSON goes to stdout; logs go to stderr.

`mcp serve` is a foreground Streamable HTTP daemon and does not require a
workspace. It listens at `http://127.0.0.1:8765/mcp` by default; see
[docs/MCP_API.md](docs/MCP_API.md). MCP bearer authorization is disabled by default
and enabled with a separate `--auth-token-file`.

The same binary and listener expose authenticated Custom GPT Actions with
`--gpt-actions --gpt-actions-token-file PATH`. A production HTTPS publishing
layer must expose only `/api/gpt/v1/*`; `/mcp`, health routes, and Joplin remain
private. The headless installer always enables Actions in the single
`joplin-md-sync.service`, generates separate Actions and MCP bearer tokens, and
always enables MCP authentication. See [docs/SERVICE.md](docs/SERVICE.md).

Deletions are **never propagated by default** — they are reported. Pass
`--propagate-deletes` to apply them (local files go to quarantine under
`.joplin-sync/quarantine/`; remote notes go to the normal Joplin trash,
never permanent deletion).

## Exit codes (stable)

| Code | Meaning |
| --- | --- |
| 0 | success; no differences where relevant |
| 1 | differences or pending actions found (`--dry-run`, `diff --exit-code`) |
| 2 | unresolved conflicts present |
| 3 | invalid workspace or malformed managed file |
| 4 | Joplin API unavailable or authentication failed |
| 5 | concurrent modification detected (or workspace locked) |
| 6 | partial operation; recovery required |
| 7 | unsafe operation blocked (explicit flag missing) |
| 8 | tool outdated (`update-check` only) |
| 9 | internal failure |

JSON responses always contain `code` (e.g. `OK`, `DIFF_FOUND`,
`CONFLICTS_PRESENT`, `API_AUTH_FAILED`, `CONCURRENT_MODIFICATION`,
`RECOVERY_REQUIRED`) plus `success`, `exit_code`, `schema_version`.

## Conflicts

A divergent concurrent edit never overwrites either side. It produces exit
code 2 and a bundle under `.joplin-sync/conflicts/<id>/` with `base.md`,
`local.md`, `remote.md`, `metadata.json`:

```bash
joplin-md-sync conflicts list --root ./notes --json
joplin-md-sync conflicts show CONFLICT_ID --root ./notes --json
joplin-md-sync conflicts resolve CONFLICT_ID --take-local
joplin-md-sync conflicts resolve CONFLICT_ID --take-remote
joplin-md-sync conflicts resolve CONFLICT_ID --merged-file PATH
joplin-md-sync conflicts discard CONFLICT_ID
```

Resolution re-validates both sides first; if anything changed since the
bundle was created it refuses (exit 5) — rerun `sync` and resolve again.

## Recovery

If a run is interrupted, the next mutating command fails with exit 6:

```bash
joplin-md-sync recover --root ./notes --json
```

`recover` checks which journaled operations verifiably completed, settles
the journal, and unblocks the workspace. Then rerun the original command.
Overwritten files are backed up under `.joplin-sync/backups/<run-id>/`.

## Safety prohibitions

- Never edit anything under `.joplin-sync/`.
- Never remove or hand-edit the first-line metadata comment's `id`.
- Run `pull` before editing; run `diff` and `push --dry-run` before `push`.
- Never resolve a conflict by deleting bundle files manually — use
  `conflicts resolve` / `conflicts discard`.
- Never touch Joplin's own database, profile directory, or sync directory.
- Never use the Joplin token in shell arguments or commit it anywhere.

## Complete example session

```console
$ joplin-md-sync pull --root ./notes --json
{"code": "OK", "command": "pull", "exit_code": 0, ... "execution": {"applied": 3, ...}}
$ echo "extra line" >> "notes/Work/Kubernetes--17a35454.md"   # (after the header!)
$ joplin-md-sync push --root ./notes --dry-run --json
{"code": "PENDING_ACTIONS", "exit_code": 1, "planned_operations": [
  {"op_id": "op-0001", "kind": "push_update_remote", "fields": ["body"],
   "path": "Work/Kubernetes--17a35454.md"}], ...}
$ joplin-md-sync push --root ./notes --json
{"code": "OK", "exit_code": 0, "execution": {"applied": 1, "failed": 0}, ...}
$ joplin-md-sync diff --root ./notes --exit-code; echo "exit=$?"
exit=0
```

Details: [docs/CLI.md](docs/CLI.md),
[docs/WORKSPACE_FORMAT.md](docs/WORKSPACE_FORMAT.md),
[docs/STATE_MODEL.md](docs/STATE_MODEL.md),
[docs/CONFLICTS.md](docs/CONFLICTS.md).
