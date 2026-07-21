# Joplin notes repository - agent runbook

This repository is a safe, Git-reviewable working copy of a user's Joplin
notes. The normal mode is file based: pull Joplin into `notes/`, edit Markdown
with ordinary repository tools, review the three-way diff, dry-run, and push.
MCP is optional and must not be mixed with an unfinished file-based task.

## First contact: help the user enable access

Before installing, initializing, or syncing anything, make sure the user has
done the following. Explain these steps rather than asking them to paste a
secret into chat:

1. Start Joplin Desktop and open **Tools > Options > Web Clipper**.
2. Enable **Web Clipper Service** and wait for its status to show that it is
   running. The normal local endpoint is `http://127.0.0.1:41184`.
3. Copy the **Authorization token** displayed on the same page. It is not the
   user's Joplin Cloud password or end-to-end-encryption password.
4. Store it as the only line in `./.secrets/joplin-token`. The user can use the
   masked commands in `README.md`; the agent must not request, read, print, or
   log the value.
5. Verify `.secrets/joplin-token` is ignored by Git before proceeding.

Pass the credential as `--token-file ./.secrets/joplin-token`. Never put a
token in a shell argument, environment file, tracked config, commit, issue,
chat message, or generated report. The `.secrets/` directory is outside the
sync workspace (`./notes`) and ignored by the repository.

Joplin Desktop must remain running for online CLI or MCP operations.

## Local command

Use the repository-local standalone executable, never an unverified command
found on `PATH`:

| Platform | Command |
| --- | --- |
| Linux | `./.tools/joplin-md-sync` |
| Windows PowerShell | `& .\.tools\joplin-md-sync.exe` |

If it is absent, run `python3 scripts/install-joplin-md-sync.py` on Linux or
`python scripts\install-joplin-md-sync.py` on Windows. The installer resolves
the latest stable GitHub Release and verifies its checksum, origin,
distribution kind, and version before replacing anything. Then run `version
--json`. Run `update-check --json` at the start of later sessions; exit code 8
means the local binary is old, so rerun the installer. A network failure during
the freshness check is not permission to replace a working binary from an
unverified source.

Use `--json` for decisions. JSON belongs on stdout and diagnostics on stderr.
Exit 1 from `push --dry-run` means that planned actions exist and is expected;
do not hide it behind an `&&` chain or treat every nonzero exit as an internal
failure.

## Non-negotiable safety rules

- Never edit, delete, copy between repositories, or commit anything under
  `notes/.joplin-sync/`.
- Never edit or remove an existing note header's `id`. Preserve the complete
  first-line `<!-- joplin-md-sync: ... -->` comment and the blank line after it.
- Never touch Joplin's database, profile, or sync-target directories.
- Never discard pre-existing working-tree changes. Inspect `git status
  --short` before work and preserve unrelated changes.
- Pull before starting a new edit. Never push before reviewing both `diff
  --three-way --unified` and `push --dry-run --json`.
- Never pass `--propagate-deletes` without separate, explicit user approval for
  the exact deletions shown by the dry-run.
- Never resolve or discard a conflict merely to unblock a command. Show the
  relevant local/remote difference and get the user's choice.
- Do not commit or push Git history unless the user asks. Git and Joplin pushes
  are separate operations.

## Session procedure

### 1. Preflight

1. Inspect `git status --short`; do not clean or reset the tree.
2. Verify the local binary with `version --json` and check freshness with
   `update-check --json`.
3. If `notes/.joplin-sync/workspace.json` does not exist, initialize once with
   `init --root ./notes --mode remote-first`. Use `local-first` only for a
   deliberate import of pre-existing Markdown and follow `RUNBOOK.md`.
4. Run:

   ```bash
   ./.tools/joplin-md-sync doctor --root ./notes \
     --token-file ./.secrets/joplin-token --json
   ./.tools/joplin-md-sync pull --root ./notes \
     --token-file ./.secrets/joplin-token --json
   ```

5. If pull reports conflicts, recovery requirements, invalid files, or a lock,
   stop the edit workflow and handle that state first.

PowerShell uses the same arguments with `& .\.tools\joplin-md-sync.exe`.

### 2. Locate and edit notes

- Search with normal file tools under `notes/`, excluding
  `notes/.joplin-sync/`. Prefer exact note titles and metadata over guessing by
  file name.
- Existing note identity comes from the header, not the file name. Edit the
  Markdown body below the header.
- Change a title or tags with `note set-title PATH TITLE` or `note set-tags
  PATH TAG...`; these commands rewrite the header atomically.
- Create a note as a plain `.md` file inside an existing notebook directory.
  Its filename becomes its initial title. Never create a note in `notes/`
  itself because Joplin has no root notes.
- Create a notebook as a directory under `notes/` or another notebook. On push,
  the directory is adopted and receives `.joplin-folder.json`.
- Move an existing note to another managed notebook directory to change its
  notebook. Keep its metadata header intact.
- Leave every `:/resource-id` and `:/note-id` link unchanged unless the user's
  requested edit specifically requires changing the link.

Validate each changed note with `note validate PATH --json`. A headerless new
note is valid and is reported as a new unmanaged file that push will adopt.

### 3. Review and synchronize

Run both reviews after the edits:

```bash
./.tools/joplin-md-sync diff --root ./notes --three-way --unified \
  --token-file ./.secrets/joplin-token
./.tools/joplin-md-sync push --root ./notes --dry-run \
  --token-file ./.secrets/joplin-token --json
```

Read every planned operation and confirm it matches the requested note scope.
Do not push if the plan contains unexplained notes, malformed files, conflicts,
or deletions.

An explicit request to update or synchronize specified Joplin notes authorizes
a normal, non-deleting push after the dry-run matches the reviewed edits. If
the user requested only local drafting or inspection, stop after the dry-run
and ask before pushing. Deletion propagation always requires a new, explicit
confirmation even when the overall task mentioned synchronization.

For an authorized normal push:

```bash
./.tools/joplin-md-sync push --root ./notes \
  --token-file ./.secrets/joplin-token --json
./.tools/joplin-md-sync status --root ./notes --json
./.tools/joplin-md-sync diff --root ./notes --exit-code \
  --token-file ./.secrets/joplin-token --json
```

Report what changed, whether Joplin was updated, and any remaining local Git
changes. Do not claim success until the push reports zero failed operations and
the final comparison is clean.

## Exit codes and exceptional states

| Exit | Meaning | Required response |
| --- | --- | --- |
| 0 | success / clean | Continue. |
| 1 | differences or dry-run actions | Review the reported differences/actions. |
| 2 | unresolved conflicts | Use `conflicts list/show`; do not push through it. |
| 3 | invalid workspace or file | Correct the reported path without touching internal state. |
| 4 | Joplin/API/auth unavailable | Check that Joplin and Web Clipper are running; let the user repair the token file. |
| 5 | concurrent change or lock | Let the other operation finish, then pull/re-plan. |
| 6 | interrupted run | Run `recover --root ./notes --json`, inspect its result, then rerun the original command. |
| 7 | unsafe operation blocked | Obtain the specific permission or use the safe workflow; do not bypass casually. |
| 8 | newer tool release | Rerun the verified installer. |
| 9 | internal failure | Preserve diagnostics and stop mutating operations. |

Conflict bundles are managed only through `conflicts list`, `conflicts show`,
`conflicts resolve`, and `conflicts discard`. Resolution rechecks both sides;
if it returns exit 5, pull again and reconsider the new state. Never hand-edit
or delete bundle files.

## Optional MCP mode

The file workflow above is the default because Git diffs, batch text tools,
offline work, dry-runs, three-way classification, and explicit conflict
bundles make substantial edits reviewable. MCP is useful when the user needs
immediate structured search or CRUD across notes, notebooks, tags, and binary
resources without materializing a workspace.

To help a user connect an MCP-capable agent:

1. Confirm the client supports **MCP Streamable HTTP**. This server is not an
   stdio MCP server.
2. Start it in a dedicated terminal:

   ```bash
   ./.tools/joplin-md-sync mcp serve \
     --token-file ./.secrets/joplin-token
   ```

   The default endpoint is `http://127.0.0.1:8765/mcp`. Keep the process and
   Joplin Desktop running.
3. Add a project or user MCP connection using the client's documented config.
   Client schemas differ, but the conceptual connection is:

   ```json
   {
     "type": "streamable-http",
     "url": "http://127.0.0.1:8765/mcp"
   }
   ```

4. Reload the client, enumerate the `joplin_*` tools, and verify access first
   with a read-only notebook or note-list call.
5. Keep the listener on loopback. If MCP bearer auth is desired, create a
   separate `.secrets/mcp-token` with
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, restrict it
   to mode `0600`, start with `--auth-token-file`, and configure the client's
   `Authorization: Bearer ...` header without exposing it. Never reuse the
   Joplin token as the MCP token.

MCP mutations are immediate and do not provide the workspace's Git review or
file-sync dry-run. Choose one write mode for a task. After any MCP mutation,
pull the file workspace before editing it; never make MCP writes while local
file changes are awaiting push. Do not bind MCP to a non-loopback address as
an ad hoc way to reach a remote agent.

For client details and persistent service setup, consult the upstream
[MCP API](https://github.com/kogeler/joplin-md-sync/blob/main/docs/MCP_API.md)
and [service guide](https://github.com/kogeler/joplin-md-sync/blob/main/docs/SERVICE.md).
