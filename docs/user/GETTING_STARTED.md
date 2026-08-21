# Installation and quick start

The behavioral guarantees used by this tutorial are defined by the
[contract catalog](../contracts/README.md).

This path connects a local Joplin Desktop installation to a reviewable
Markdown workspace. It is the best first setup even if you plan to add MCP
later, because it makes the state and safety model concrete.

## Prerequisites

- Windows or Linux.
- Joplin Desktop running locally.
- **Tools > Options > Web Clipper > Enable Web Clipper Service** enabled.
- The authorization token shown on that Joplin settings page.
- CPython 3.13 or 3.14 for source, wheel, or zipapp installs. Native release
  executables include Python.

!!! warning "Use the Web Clipper token"

    The required value is not a Joplin Cloud password or an end-to-end
    encryption password. Never paste it into an agent chat, a command argument,
    a Git-tracked file, or a log.

## 1. Install

=== "pipx"

    ```bash
    pipx install "joplin-md-sync==1.5.5"
    joplin-md-sync version --json
    ```

=== "pip"

    ```bash
    python -m pip install "joplin-md-sync==1.5.5"
    joplin-md-sync version --json
    ```

=== "Native executable"

    Download the matching asset and `SHA256SUMS.txt` from the
    [latest GitHub release](https://github.com/kogeler/joplin-md-sync/releases/latest).
    Verify its checksum before running it. On Linux:

    ```bash
    chmod +x joplin-md-sync-linux-amd64
    ./joplin-md-sync-linux-amd64 version --json
    ```

=== "Zipapp"

    Download `joplin-md-sync.pyz` from the
    [latest GitHub release](https://github.com/kogeler/joplin-md-sync/releases/latest),
    then run:

    ```bash
    python joplin-md-sync.pyz version --json
    ```

Check whether a newer stable release exists:

```bash
joplin-md-sync update-check --json
```

Exit code 8 means an update is available; it is not an execution failure and
the tool never updates itself.

## 2. Provide the token

For a short interactive session, export the token:

=== "Linux"

    ```bash
    read -rsp 'Joplin Web Clipper token: ' JOPLIN_TOKEN; printf '\n'
    export JOPLIN_TOKEN
    ```

=== "PowerShell 7"

    ```powershell
    $env:JOPLIN_TOKEN = Read-Host -MaskInput "Joplin Web Clipper token"
    ```

For a repository or recurring workflow, prefer a protected ignored token file
outside the sync workspace. The
[agent notes repository template](https://github.com/kogeler/joplin-md-sync/tree/main/examples/agent-notes-repository)
includes a complete setup.

Only the token is required with normal Joplin Desktop settings. The default
endpoint is `http://127.0.0.1:41184`; the CLI also discovers local Joplin
ports 41184 through 41194.

## 3. Initialize from Joplin

Use `remote-first` when Joplin already contains the notes you want to manage:

```bash
joplin-md-sync init --root ./notes --mode remote-first
joplin-md-sync doctor --root ./notes --json
joplin-md-sync pull --root ./notes --json
joplin-md-sync status --root ./notes --json
```

`remote-first` refuses to adopt existing unmanaged Markdown. This prevents an
accidental first push from duplicating local files as new Joplin notes.

The generated workspace contains:

```text
notes/
├── Notebook/
│   ├── .joplin-folder.json
│   └── Example-note--17a35454.md
└── .joplin-sync/                 # local state; ignored; never edit
```

Every managed note begins with a one-line identity header. Keep it intact:

```markdown
<!-- joplin-md-sync: {"id":"17a35454fbb34ee080e29fba9ee88730","schema":1,"tags":["ops"],"title":"Example note"} -->

The exact Joplin Markdown body starts here.
```

## 4. Make and review one change

Edit only the body below the metadata header, then run:

```bash
joplin-md-sync diff --root ./notes --three-way --unified
joplin-md-sync push --root ./notes --dry-run --json
```

A dry-run with pending work exits 1 and returns
`"code": "PENDING_ACTIONS"`. Review `planned_operations`, then apply the same
plan:

```bash
joplin-md-sync push --root ./notes --json
joplin-md-sync status --root ./notes --json
```

The real push re-checks both sides before each write, applies the operation,
reads the result back, and only then updates the base snapshot.

## 5. Hand the workspace to an agent

Use the copyable
[`examples/agent-notes-repository`](https://github.com/kogeler/joplin-md-sync/tree/main/examples/agent-notes-repository)
template for a guarded `AGENTS.md`, human runbook, `.gitignore`, protected
credential layout, and verified native-release installer.

Ask for the note outcome, not low-level file operations:

> Update the rollback section in my deployment note from the current project,
> show the three-way diff and planned Joplin operations, then synchronize the
> approved result.

The [Agent workflows](AGENT_WORKFLOWS.md) page covers routine changes,
Git-reviewed knowledge, conflicts, deletions, and when to use MCP instead.

## Choose the next path

| Goal | Next page |
| --- | --- |
| Understand every CLI flag and result | [CLI reference](CLI.md) |
| Connect an MCP-capable client | [MCP API](MCP_API.md) |
| Build a private Joplin Custom GPT | [ChatGPT Actions](CHATGPT_ACTIONS.md) |
| Run Joplin and the bridge on Linux | [Self-hosted deployment](SELF_HOSTED.md) |
| Understand conflicts and recovery | [Synchronization design](../maintenance/SYNCHRONIZATION.md) and [Conflict handling](CONFLICTS.md) |
