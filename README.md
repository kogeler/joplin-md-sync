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

Requires CPython **>= 3.13** on Windows or Linux. Zero runtime dependencies.

```bash
python -m pip install "git+https://github.com/kogeler/joplin-md-sync.git@v1.0.0"
# or: pipx install "git+https://github.com/kogeler/joplin-md-sync.git@v1.0.0"
# or download joplin-md-sync.pyz from a release and: python joplin-md-sync.pyz --help
```

From a checkout, everything is driven by the Makefile:

```bash
make venv        # runtime venv/ with the CLI installed (venv/bin/joplin-md-sync)
make venv-dev    # tooling venv-dev/ (ruff, mypy, build) from the pinned lock
make check       # lint + typecheck + full test suite
make package     # dist/: wheel, sdist, standalone .pyz, SHA-256 checksums
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

## Architecture overview

```
cli  ->  planner (pure three-way classification: base/local/remote)
     ->  executor (guard -> apply -> verify -> commit base, journaled)
api: stdlib urllib client for the Joplin Data API (pagination, retries)
state: SQLite base snapshots, conflicts, tombstones, run journal
workspace: scanning, atomic writes, backups, quarantine, cross-platform lock
```

Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Supported / not supported (v1)

| Supported | Out of scope |
| --- | --- |
| notes, notebooks (nested), tags | editing/uploading binary attachments |
| two-way sync with conflict bundles | Nextcloud/WebDAV or any direct sync target |
| resource download for inspection | replacing Joplin's own device sync |
| crash recovery, backups, quarantine | automatic text merging (only explicit `--merged-file`) |
| Windows + Linux, Python 3.13/3.14 | daemon/watch mode, mobile, self-update, permanent deletion |

## Versioning

Semantic versioning; Git tags `vX.Y.Z` with GitHub releases carrying the
wheel, sdist, `.pyz`, and SHA-256 checksums. `joplin-md-sync update-check
--json` compares the installed version against the latest stable release
(exit 8 when outdated). JSON output, exit codes, and the state schema are
versioned and stable across patch releases. See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).
