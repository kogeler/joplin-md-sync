# Operations runbook

This is the human-facing companion to `AGENTS.md`. Commands below use Linux
syntax. In PowerShell, set `$TOOL = ".\.tools\joplin-md-sync.exe"` and replace
`"$TOOL"` with `& $TOOL`.

## Access checklist

Before the first command, start Joplin Desktop, open **Tools > Options > Web
Clipper**, enable **Web Clipper Service**, and wait for it to report that it is
running. Copy the page's **Authorization token** into the ignored
`.secrets/joplin-token` file as the only line. Use the masked setup commands in
`README.md`; never paste the token into an agent chat or command argument.

The default service address is `http://127.0.0.1:41184`. Only add `--port` or
`--base-url` when the Web Clipper page shows a non-default local setting. Leave
Joplin Desktop running during online operations.

```bash
TOOL=./.tools/joplin-md-sync
AUTH=--token-file=./.secrets/joplin-token
```

## First import from Joplin

Use this path when Joplin already contains the authoritative notes:

```bash
python3 scripts/install-joplin-md-sync.py
"$TOOL" init --root ./notes --mode remote-first
"$TOOL" doctor --root ./notes "$AUTH" --json
"$TOOL" pull --root ./notes "$AUTH" --json
"$TOOL" status --root ./notes --json
git add notes
git commit -m "Import notes from Joplin"
```

The generated `notes/.gitignore` is safe to commit. The ignored
`notes/.joplin-sync/` is machine-local state and must never be copied, edited,
or committed.

## Routine editing session

```bash
git status --short
"$TOOL" doctor --root ./notes "$AUTH" --json
"$TOOL" pull --root ./notes "$AUTH" --json
# edit Markdown under notes/<notebook>/
"$TOOL" diff --root ./notes --three-way --unified "$AUTH"
"$TOOL" push --root ./notes --dry-run "$AUTH" --json
# Review the exact plan. Exit 1 means pending actions and is expected.
"$TOOL" push --root ./notes "$AUTH" --json
"$TOOL" status --root ./notes --json
```

Commit the resulting Markdown and `.joplin-folder.json` changes only after the
Joplin push is verified, and only when Git history is part of the desired
workflow.

## Creating, renaming, tagging, and moving

- New note: create `notes/<notebook>/<title>.md` as plain Markdown. Push adds
  the metadata header and canonical filename.
- New notebook: create a directory below `notes/` or an existing notebook.
  Push creates it and writes `.joplin-folder.json`.
- Rename a note safely:
  `"$TOOL" note set-title PATH "New title" --json`.
- Replace its tags safely:
  `"$TOOL" note set-tags PATH tag1 tag2 --json`.
- Move a note: move its complete file, including the header, into another
  managed notebook directory. The next dry-run must show a parent/notebook
  change for that note.
- Validate before push: `"$TOOL" note validate PATH --json`.

Filenames are cosmetic; identity is the header `id`. Never duplicate a managed
file to make a new note because that duplicates the id. For a new note, create
a plain file or use a generated header without an `id`.

## Importing pre-existing Markdown

`local-first` is only for a deliberate migration where local Markdown should
become new Joplin notes. Put every note inside a notebook directory, then:

```bash
"$TOOL" init --root ./notes --mode local-first
"$TOOL" doctor --root ./notes "$AUTH" --json
"$TOOL" push --root ./notes --dry-run "$AUTH" --json
```

Review every proposed creation. The first real local-first push is guarded by
the required dry-run. Do not continue if existing Joplin notes would be
duplicated; use remote-first and merge the local content deliberately instead.

## Deletions

Deleting a local file or deleting a note in Joplin is reported but not
propagated by default. First run the normal dry-run and identify every deletion.
Only after explicit approval for those exact paths run the appropriate command
with `--propagate-deletes` and repeat the dry-run before the real operation.

Propagated local removals go to `.joplin-sync/quarantine/`; propagated remote
note removals go to Joplin trash, not permanent deletion. Notebook deletion is
not propagated in version 1.

## Conflicts

```bash
"$TOOL" conflicts list --root ./notes --json
"$TOOL" conflicts show CONFLICT_ID --root ./notes --json
```

Compare base, local, and remote content with the user. Then choose exactly one:

```bash
"$TOOL" conflicts resolve CONFLICT_ID --root ./notes --take-local "$AUTH" --json
"$TOOL" conflicts resolve CONFLICT_ID --root ./notes --take-remote "$AUTH" --json
"$TOOL" conflicts resolve CONFLICT_ID --root ./notes --merged-file PATH "$AUTH" --json
```

Do not delete or edit bundle files manually. If resolution returns exit 5,
either side changed again; pull and review the new conflict state.

## Interrupted operations

Exit 6 means an operation journal needs recovery:

```bash
"$TOOL" recover --root ./notes --json
"$TOOL" doctor --root ./notes "$AUTH" --json
```

Recovery verifies what completed and unblocks the workspace; it does not blindly
replay writes. Inspect its JSON, then rerun the original pull or push.

## Updating the local tool

```bash
"$TOOL" update-check --json
# Exit 8 means a newer stable release exists.
python3 scripts/install-joplin-md-sync.py
"$TOOL" version --json
```

The binary stays under ignored `.tools/` and is never committed. The installer
refuses unsupported platforms, unexpected release URLs, checksum mismatches,
and existing unrecognized files.

## Optional MCP connection

MCP is useful for structured search, tags, notebooks, attachments, and
immediate operations when a local Git-reviewed Markdown transformation is not
needed:

```bash
"$TOOL" mcp serve "$AUTH"
# Streamable HTTP endpoint: http://127.0.0.1:8765/mcp
```

Configure that URL in an MCP client that supports Streamable HTTP, reload it,
and test with a read-only `joplin_list_notebooks` or `joplin_list_notes` call.
The exact configuration file is client-specific; `AGENTS.md` includes the
conceptual JSON and security constraints.

Do not interleave MCP writes with unpushed local edits. MCP changes are
immediate and lack the file workflow's Git diff and dry-run. After MCP writes,
run `pull` before starting any local edit.
