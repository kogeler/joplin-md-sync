# Workspace Format

This page shows how to work with a managed workspace. The authoritative file,
identity, path, and state requirements are in the
[workspace contract](../contracts/WORKSPACE.md).

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

Header validation and content canonicalization are specified by
[`WSP-001`](../contracts/WORKSPACE.md#wsp-001-managed-note-headers-are-strict-and-deterministic)
and
[`WSP-002`](../contracts/WORKSPACE.md#wsp-002-canonicalization-preserves-user-content).
For normal editing, keep the first line intact and edit only the body below the
blank line. To create a note, add plain Markdown inside a notebook directory;
the first successful push writes its assigned metadata header.

## Filenames

Generated names combine the readable title with a short ID suffix. Treat them
as cosmetic and portable presentation, not identity; see
[`WSP-003`](../contracts/WORKSPACE.md#wsp-003-generated-paths-are-portable-and-confined)
and
[`WSP-004`](../contracts/WORKSPACE.md#wsp-004-identity-is-independent-of-cosmetic-paths).

## Notebook directories

Each managed directory holds `.joplin-folder.json`:

```json
{"id": "a37dfe02...", "parent_id": "", "schema": 1, "title": "Work"}
```

Rename or move a notebook by editing `title` or `parent_id` in this file, or do
it in Joplin and pull. To create a notebook locally, create a directory and put
the new Markdown files inside it. The exact identity and creation behavior is
owned by [`WSP-004`](../contracts/WORKSPACE.md#wsp-004-identity-is-independent-of-cosmetic-paths).

## `.joplin-sync/` internals

Do not edit or commit this directory. It contains the base state, workspace
configuration, journals, backups, quarantine, conflict evidence, and downloaded
resources. The complete ownership and failure rules are in
[`WSP-005`](../contracts/WORKSPACE.md#wsp-005-internal-state-is-reserved-recoverable-and-token-free),
with recovery behavior in
[`SYN-008`](../contracts/SYNCHRONIZATION.md#syn-008-locking-and-journals-make-interruption-explicit).
