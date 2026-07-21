# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-07-21

### Added

- Add a copyable agent-notes repository template with a guarded Markdown sync
  runbook and verified latest standalone installer.

### Changed

- Make the headless installer generate separate Actions and MCP bearer tokens,
  always enable MCP authentication, and report only the protected token-file
  paths after successful installation.

### Fixed

- Validate generated and preserved Actions tokens using the same minimum
  decoded 32-byte length as the running service.
- Harden MCP and Actions authentication against weak or unsafe token files,
  non-ASCII and duplicated Authorization headers, malformed URL targets,
  deeply nested JSON, and unbounded pre-authentication connection threads.

## [1.3.0] - 2026-07-20

### Added

- Optional authenticated ChatGPT Custom GPT Actions routes in the existing MCP
  listener, backed by the same tool registry, JSON Schema validator, executor,
  Joplin service, and client factory.
- Deterministic on-demand OpenAPI 3.1 export, dedicated
  rotatable token-file authentication, request/response limits, bounded
  concurrency, rate limiting, health endpoints, and redacted audit metadata.
- Separate service deployment and Custom GPT editor guides, paste-ready GPT
  instructions, and a unified systemd installer.
- Opt-in real-Joplin GPT Actions acceptance coverage for every exposed tool,
  credential isolation and rotation, transport and validation failures, limits,
  unavailable upstreams, and failure-safe cleanup of randomized test content.

### Changed

- Consolidate MCP and Actions deployment into one `joplin-md-sync.service`,
  with a mandatory separate Actions token and optional MCP bearer token, and
  consolidate service installation and operations into `docs/SERVICE.md`.
- Name the combined adapter release selector `--joplin-md-sync-version` and
  include the complete headless service installer in source distributions.

### Fixed

- Validate notebook icons as JSON-serialized Joplin `FolderIcon` objects before
  any create or update request, preventing malformed icon strings from crashing
  the Joplin Desktop sidebar.

## [1.2.0] - 2026-07-18

### Added

- Foreground MCP Streamable HTTP server (`mcp serve`) with tools for notebook
  and note listing, full note/metadata reads, create/update/tag/trash actions,
  and Joplin full-text search.
- Complete MCP CRUD for nested notebooks and tags, note/notebook restore,
  entity relationship traversal, and explicit permanent-delete annotations for
  tags and resources.
- HTML note creation plus binary resource list/read/upload/update/delete and
  one-call note creation with bounded base64 attachments and generated Joplin
  `:/resource-id` links.
- Resilient Joplin availability handling for MCP: lazy startup, bounded retry
  waits, structured retryable tool errors, and recovery without daemon restart.
- Optional independent MCP bearer authorization with token-file rotation,
  loopback/Origin protections, a systemd user unit, and a Windows Task
  Scheduler installer.
- Opt-in `make test-live` acceptance suite against a real local Joplin profile.
  It uses randomized object names, refuses to update/delete non-owned note IDs,
  verifies pre-existing note state, and cleans up only its own objects.

## [1.1.0] - 2026-07-18

### Added

- Native one-file executables for Linux AMD64, Linux ARM64, and Windows AMD64.
  Each executable is built and smoke-tested on its target GitHub Actions runner
  before inclusion in the release and the shared `SHA256SUMS.txt` inventory.

## [1.0.0] - 2026-07-17

### Added

- Two-way synchronization (`pull`, `push`, `sync`) between the local Joplin
  Data API and a Markdown workspace with true three-way (base/local/remote)
  classification.
- Managed Markdown format: one-line JSON metadata header (id, schema, tags,
  title); notebooks as directories with `.joplin-folder.json`.
- SQLite state database with full base snapshots, tombstones, conflict
  records, and an operation journal with crash recovery (`recover`).
- Optimistic concurrency: pre-write revalidation of both sides, post-write
  verification, per-note abort on concurrent modification (exit code 5).
- Conservative deletion policy: reported by default, applied only with
  `--propagate-deletes` (local quarantine / Joplin trash, never permanent).
- Conflict bundles (`base.md`, `local.md`, `remote.md`, `metadata.json`)
  with staleness-checked `resolve --take-local | --take-remote |
  --merged-file` and `discard`.
- Non-mutating `diff` with `--summary`, `--name-status`, `--unified`,
  `--three-way`, `--against`, `--note`, `--exit-code`, `--offline`.
- Agent contract: deterministic `--json` envelopes on stdout, logs on
  stderr, stable exit codes 0–9, `capabilities`, `agent-manifest.json`.
- `doctor`, `status`, `note set-title/set-tags/validate`,
  `resources pull`, `update-check` (GitHub releases), port discovery
  (41184–41194), token redaction everywhere.
- Packaging: wheel, sdist, standalone `.pyz` zipapp; `scripts/bootstrap.py`;
  CI and release workflows for Windows and Linux.
- Built-in default endpoint `http://127.0.0.1:41184`: only `JOPLIN_TOKEN`
  needs to be configured; port discovery (41184–41194) remains as fallback
  and CLI/env/workspace settings override it.

### Changed

- Project tooling reworked around a Makefile (`make help`): two separate
  local virtual environments — `venv/` (runtime, package installed
  editable) and `venv-dev/` (ruff/mypy/pytest/build) — with dev tools
  declared in `[dependency-groups]` and pinned via a full `pip freeze` lock in
  `requirements-dev.txt` (`make freeze`). CI and the release workflow reuse
  the same Makefile targets.
- The version's single source moved to the root `.version` file:
  `pyproject.toml` reads it dynamically and `__version__` is resolved at
  runtime (zipapp embeds a copy; wheels use distribution metadata).
- Tests run in four isolated pytest-xdist workers by default; set
  `TEST_WORKERS=N` to override the Makefile default.
- CI runs the built `.pyz` explicitly on every Windows/Linux and Python
  matrix entry. Wheel and zipapp smoke tests no longer rebuild artifacts.
- Merges to `main` create an annotated `vX.Y.Z` tag from `.version` and
  publish the corresponding GitHub release. Pull requests require a strict
  version increment relative to their base commit.

### Fixed

- CLI log handlers and failed SQLite connections are closed deterministically,
  preventing `WinError 32` failures during temporary-directory cleanup.
- Restoring a trashed note during conflict resolution now uses
  `PUT deleted_time=0` — real Joplin rejects `POST /notes` with an existing
  id (UNIQUE constraint). Recreation via POST is kept for permanently
  deleted notes.
- `resources pull` no longer misreports Joplin note-to-note links
  (same `:/id` syntax) as missing resources.
- The fake Joplin test server was aligned with observed real-Joplin
  behavior: POST with an existing id fails, single-note GET returns trashed
  notes, PUT applies to trashed notes.
