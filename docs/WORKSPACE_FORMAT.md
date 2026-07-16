# Workspace Format

```
my-notes/
├── Work/
│   ├── .joplin-folder.json
│   ├── Kubernetes--17a35454.md
│   └── Operations--912f01ac.md
├── Personal/
│   ├── .joplin-folder.json
│   └── Plans--27b8f102.md
├── .joplin-sync/            # internal state — never edit, never commit
│   ├── state.sqlite3
│   ├── workspace.json
│   ├── lock
│   ├── journal/  backups/  conflicts/  quarantine/  resources/
└── .gitignore               # generated; ignores .joplin-sync/
```

## Managed note files

First line: a single-line HTML comment with compact, key-sorted JSON.
Then exactly one blank line. Then the byte-exact Joplin Markdown body
(line endings normalized to LF).

```markdown
<!-- joplin-md-sync: {"id":"17a35454fbb34ee080e29fba9ee88730","schema":1,"tags":["homelab","kubernetes"],"title":"Kubernetes"} -->

The exact Joplin Markdown body begins here.
```

Rules:

- keys: `id` (32-hex, optional — absent means *new local note*), `schema`
  (currently `1`), `tags` (lowercase, sorted), `title`. Unknown keys make
  the file invalid.
- `-->` inside values is emitted as `-->` so the comment cannot
  terminate early; JSON parsing restores it.
- No volatile fields (timestamps) live in the header, so unchanged notes
  are byte-stable across pulls.
- The body is never reformatted; embedded HTML, `:/resource` and `:/note`
  links, trailing whitespace, and Unicode are preserved verbatim.
- A file with **no header** inside a notebook directory is treated as a new
  note (title = file name without `.md`). A file with a **malformed
  header** is `INVALID_LOCAL_FILE` and blocks push for that file only.

## Filenames

`<sanitized-title>--<first 8 chars of id>.md`. Sanitization handles
Windows-invalid characters, reserved device names (`CON`, `NUL`, ...),
trailing dots/spaces, length limits, and preserves Unicode. Names are
cosmetic: renaming a file changes nothing (pull normalizes it back);
identity is the header id.

## Notebook directories

Each managed directory holds `.joplin-folder.json`:

```json
{"id": "a37dfe02...", "parent_id": "", "schema": 1, "title": "Work"}
```

- Folder identity = `id`. The directory name derives from `title` and is
  normalized on pull (sibling case-insensitive collisions get an `--id`
  suffix).
- Rename/move a notebook by editing `title`/`parent_id` in this file (or
  do it in Joplin and pull).
- A directory **without** the file is a candidate new notebook; push
  creates it in Joplin and writes the file.
- Notes must live inside notebook directories, never in the workspace root
  (Joplin has no root notes).

## `.joplin-sync/` internals

- `state.sqlite3` — see ARCHITECTURE.md. Authoritative sync history; if it
  is lost, the metadata headers allow reconstruction: identical sides are
  re-adopted automatically, divergent ones become conflicts.
- `workspace.json` — mode, options (`backup_retention`), optional
  `base_url`. The token is **never** stored here.
- `journal/<run-id>.json` — plan + per-op status for each mutating run.
- `backups/<run-id>/` — copies of local files taken before overwrites.
- `quarantine/<run-id>/` — local files removed by `--propagate-deletes`.
- `conflicts/<conflict-id>/` — conflict bundles.
- `resources/` — downloads from `resources pull`.
