# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
