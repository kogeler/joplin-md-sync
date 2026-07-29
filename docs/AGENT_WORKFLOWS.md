# Agent workflows

There are two valid agent workflows. Choose one for a task and keep its
consistency boundary intact.

| Workflow | Best for | Review boundary | Writes |
| --- | --- | --- | --- |
| Markdown workspace | Multi-file edits, project-aware transformations, Git history, exact previews | Three-way diff and push dry-run | Deferred until explicit push |
| MCP or Actions | Search, targeted CRUD, tags, notebooks, attachments, conversational access | Client/tool request and returned result | Immediate |

Do not interleave direct MCP writes with unpushed local edits. After any direct
write, pull again before the next file-editing session.

## Guarded Markdown session

A reliable agent session follows the same order every time:

```bash
joplin-md-sync doctor --root ./notes --json
joplin-md-sync pull --root ./notes --json
# Edit managed Markdown, preserving the first-line metadata header.
joplin-md-sync diff --root ./notes --three-way --unified
joplin-md-sync push --root ./notes --dry-run --json
# Review the exact operation list.
joplin-md-sync push --root ./notes --json
joplin-md-sync status --root ./notes --json
```

The agent should stop instead of improvising when:

- `doctor` reports an invalid workspace, conflict, incomplete run, or
  unavailable Joplin API;
- a pull creates a conflict bundle;
- the dry-run contains an unexpected note, field, move, creation, or deletion;
- a managed header is malformed;
- an operation returns exit 5 for concurrent modification; or
- an interrupted run returns exit 6 and requires `recover`.

## High-value tasks

### Update notes from a repository

The agent can compare a Joplin runbook against deployment code, CI workflows,
or configuration in the current repository, then update only the stale
sections. The final answer should summarize the note-level outcome and expose
the dry-run operations before push.

Good request:

> Pull my Kubernetes operations notebook. Update only the backup and rollback
> commands from this repository, preserve the explanatory prose, show the
> three-way diff, and prepare a dry-run.

### Curate a private second brain

Use MCP when the task begins with discovery: search a topic, read a small
number of exact notes, create a summary note, or normalize tags. Use the
Markdown workflow when the task requires comparing or rewriting many note
bodies with an auditable diff.

### Keep Git history

Commit managed `.md` files and `.joplin-folder.json` after a verified push.
Never commit:

- `.joplin-sync/`;
- a Joplin, MCP, Actions, sync, or encryption credential;
- the locally installed standalone binary; or
- generated backups, conflicts, quarantine, or downloaded resources.

Git is an optional review and history layer. It is not the synchronization
base used by `joplin-md-sync`.

### Create notes and notebooks

- Create a new note as plain Markdown inside a managed notebook directory.
- Create a notebook as a new directory; push assigns its identity metadata.
- Use `note set-title` and `note set-tags` for safe header changes.
- Move a managed file with its header intact to move the Joplin note.
- Never duplicate a managed file to create a note: that duplicates its Joplin
  id.

Preview every creation or move:

```bash
joplin-md-sync note validate "notes/Work/New-note.md" --json
joplin-md-sync push --root ./notes --dry-run --json
```

### Import existing Markdown

`local-first` is a deliberate migration mode, not a shortcut around a
`remote-first` refusal:

```bash
joplin-md-sync init --root ./notes --mode local-first
joplin-md-sync doctor --root ./notes --json
joplin-md-sync push --root ./notes --dry-run --json
```

The first real push is blocked until a dry-run records the proposed set. Stop
if it would duplicate notes already present in Joplin.

## Deletions

Deleting a file or a Joplin note is reported but not propagated by default.
The agent must identify the exact deletion and obtain approval before repeating
the dry-run and real command with `--propagate-deletes`.

Propagated local removals go to quarantine under `.joplin-sync/`. Propagated
remote note removals go to Joplin trash. Notebook deletion is not propagated
in v1.

## Conflicts and concurrent changes

A base/local/remote divergence produces exit code 2 and an immutable conflict
bundle. Inspect it through the CLI:

```bash
joplin-md-sync conflicts list --root ./notes --json
joplin-md-sync conflicts show CONFLICT_ID --root ./notes --json
```

Resolve it by taking one side or providing an explicit merged file. Never edit
or delete bundle files manually:

```bash
joplin-md-sync conflicts resolve CONFLICT_ID --root ./notes --take-local --json
joplin-md-sync conflicts resolve CONFLICT_ID --root ./notes --take-remote --json
joplin-md-sync conflicts resolve CONFLICT_ID --root ./notes --merged-file PATH --json
```

Resolution revalidates both sides. Exit 5 means the bundle became stale; pull
again and review the new state.

## Recovery

Exit 6 means a journaled run did not finish. Recover before any further
mutation:

```bash
joplin-md-sync recover --root ./notes --json
joplin-md-sync doctor --root ./notes --json
```

Recovery verifies operations against current state. It does not blindly replay
writes.

## Repository template

The
[`examples/agent-notes-repository`](https://github.com/kogeler/joplin-md-sync/tree/main/examples/agent-notes-repository)
directory is the supported starting point for a dedicated agent-managed notes
repository. Its `AGENTS.md` carries the safety rules close to the files, while
the runbook covers installation, routine use, conflicts, recovery, deletions,
and optional MCP.

