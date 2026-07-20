# joplin-md-sync

Safe two-way synchronization between the local [Joplin](https://joplinapp.org/)
desktop application and an ordinary directory of Markdown files — built
primarily for **autonomous coding agents** (deterministic JSON output, stable
exit codes, explicit conflict handling), and perfectly usable by humans.

> **Safety first.** The tool never overwrites divergent edits, never deletes
> anything without an explicit flag, never uses permanent deletion in Joplin,
> verifies every write after applying it, and journals every mutating run so
> interrupted syncs are recoverable. `diff` never mutates anything.

If you are an agent (or configuring one), start with **[AGENTS.md](AGENTS.md)**.

## How it works

- Notes are plain `.md` files; each carries a one-line metadata header with
  the Joplin id, title, and tags. Notebooks are directories with a
  `.joplin-folder.json`.
- Sync state (base snapshots for true three-way comparison) lives in
  `.joplin-sync/state.sqlite3` inside the workspace — never committed to Git.
- All communication uses the documented local
  [Joplin Data API](https://joplinapp.org/help/api/references/rest_api/)
  (Web Clipper service); Joplin's own database and sync targets are never
  touched.

## Installation

Source, wheel, and zipapp installations require CPython **>= 3.13** on
Windows or Linux. Native release executables include Python and have no
external runtime dependencies.

```bash
python -m pip install "git+https://github.com/kogeler/joplin-md-sync.git@v1.3.0"
# or: pipx install "git+https://github.com/kogeler/joplin-md-sync.git@v1.3.0"
# or download joplin-md-sync.pyz from a release and: python joplin-md-sync.pyz --help
```

Native GitHub Release assets:

| Platform | Architecture | Asset |
| --- | --- | --- |
| Linux | AMD64 | `joplin-md-sync-linux-amd64` |
| Linux | ARM64 | `joplin-md-sync-linux-arm64` |
| Windows | AMD64 | `joplin-md-sync-windows-amd64.exe` |

On Linux, mark the downloaded executable as executable before running it:

```bash
chmod +x joplin-md-sync-linux-amd64
./joplin-md-sync-linux-amd64 version
```

From a checkout, everything is driven by the Makefile:

```bash
make venv        # runtime venv/ with the CLI installed (venv/bin/joplin-md-sync)
make venv-dev    # tooling venv-dev/ (ruff, mypy, pytest, PyInstaller, build)
make check       # lint + typecheck + full test suite
make test-live   # opt-in real-Joplin MCP + GPT Actions suites; reads ./token
make test TEST_WORKERS=8  # override the default four parallel test workers
make package     # wheel, sdist, .pyz, current-platform executable, checksums
make help        # all targets
```

The version's single source is the root `.version` file; runtime
dependencies are declared in `pyproject.toml` (none by design), dev tools
in `[dependency-groups]` with a pip-freeze lock in `requirements-dev.txt`.

## Five-minute quick start

1. In Joplin: *Tools > Options > Web Clipper* — enable the service, copy the
   authorization token.
2. ```bash
   export JOPLIN_TOKEN=<your token>          # Windows: set JOPLIN_TOKEN=...
   joplin-md-sync init --root ./notes
   joplin-md-sync pull --root ./notes
   ```
   The token is the only required configuration: the default endpoint
   `http://127.0.0.1:41184` is built in (override with `JOPLIN_BASE_URL`,
   `JOPLIN_PORT`, `--base-url`, or `--port` when needed).
3. Edit files under `./notes`, then:
   ```bash
   joplin-md-sync diff --root ./notes
   joplin-md-sync push --root ./notes --dry-run
   joplin-md-sync push --root ./notes
   ```
4. If both sides changed the same note, you get exit code 2 and a conflict
   bundle: `joplin-md-sync conflicts list` / `conflicts resolve ID --take-local|--take-remote|--merged-file PATH`.

## MCP and ChatGPT Actions service

One foreground `joplin-md-sync` process exposes MCP and authenticated REST
Actions from the same tool registry on different URIs. It starts while Joplin
is offline and recovers on later calls without a restart:

```bash
export JOPLIN_TOKEN=...
joplin-md-sync mcp serve --gpt-actions \
  --gpt-actions-token-file ~/.config/joplin-md-sync/gpt-actions-token
# MCP:     http://127.0.0.1:8765/mcp
# Actions: http://127.0.0.1:8765/api/gpt/v1/*

joplin-md-sync gpt-actions export-openapi \
  --server-url https://notes.example.com \
  --output ./chatgpt-action.openapi.json
```

The Actions token is mandatory when Actions are enabled and is reloaded from a
protected file. MCP bearer authentication remains optional through a separate
`--auth-token-file`. The headless installer always installs both APIs as one
`joplin-md-sync.service`; it never creates a separate Actions service.

Use [service installation and operations](docs/SERVICE.md) for Linux, Windows,
credentials, URI isolation, and live tests; [MCP API](docs/MCP_API.md) for the
tool contract; and [ChatGPT Actions setup](docs/CHATGPT_ACTIONS.md) for the
OpenAI editor.

## Architecture overview

```
cli  ->  planner (pure three-way classification: base/local/remote)
     ->  executor (guard -> apply -> verify -> commit base, journaled)
api: stdlib urllib client for the Joplin Data API (pagination, retries)
mcp/actions: two HTTP transports -> shared registry/executor -> Joplin service -> api
state: SQLite base snapshots, conflicts, tombstones, run journal
workspace: scanning, atomic writes, backups, quarantine, cross-platform lock
```

Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Supported / not supported (v1)

| Supported | Out of scope |
| --- | --- |
| notes, notebooks (nested), tags, binary attachments | editing settings, revisions, or encryption state |
| two-way sync with conflict bundles | Nextcloud/WebDAV or any direct sync target |
| resource download/upload/edit through MCP | replacing Joplin's own device sync |
| crash recovery, backups, quarantine | automatic text merging (only explicit `--merged-file`) |
| Windows + Linux, Python 3.13/3.14; MCP daemon | filesystem watch mode, native mobile CLI, self-update, permanent note/notebook deletion |

## Versioning

Semantic versioning; Git tags `vX.Y.Z` with GitHub releases carrying the
wheel, sdist, `.pyz`, native executables, and SHA-256 checksums.
`joplin-md-sync update-check --json` compares the installed version against
the latest stable release
(exit 8 when outdated). JSON output, exit codes, and the state schema are
versioned and stable across patch releases. See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
