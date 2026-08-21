# joplin-md-sync Agent Runbook

Safe two-way synchronization between Joplin and ordinary Markdown, with MCP
and ChatGPT Actions interfaces for agents. Product guarantees are defined only
by the [contract catalog](docs/contracts/README.md). This runbook tells an agent
how to operate the project safely; it does not replace those contracts.

## Requirements

- CPython 3.13 or 3.14 on Windows or Linux for source, wheel, and zipapp use.
- Joplin Desktop running locally with Web Clipper enabled, normally on port
  `41184`.
- The Joplin Web Clipper token, supplied through `JOPLIN_TOKEN` or a protected
  token file.

Native release executables include Python.

## Install

```bash
python -m pip install "joplin-md-sync==1.5.6"
# or:
pipx install "joplin-md-sync==1.5.6"
python joplin-md-sync.pyz --help
./joplin-md-sync-linux-amd64 version
```

Verify the installed version before changing notes:

```bash
joplin-md-sync version --json
joplin-md-sync update-check --json
```

## Repository development

Use Make targets so local checks use the same locked environments as CI:

```bash
make venv
make venv-dev
make check
make ci
make freeze-check
make test-service-installer
make test-live
make package
make smoke
make docs-build
make docs-audit
make docs-screenshots
make docs-serve
make help
```

Development procedures are in
[Development](docs/maintenance/DEVELOPMENT.md), dependency updates in
[Dependency maintenance](docs/maintenance/DEPENDENCIES.md), and releases in
[Releases](docs/maintenance/RELEASES.md). The root `.version` file is the human
maintained version source.

## Safe Markdown workflow

```bash
export JOPLIN_TOKEN=...
joplin-md-sync init --root ./notes --mode remote-first    # first run only
joplin-md-sync doctor --root ./notes --json
joplin-md-sync pull --root ./notes --json
# Edit managed Markdown without changing an existing metadata id.
joplin-md-sync diff --root ./notes --three-way --unified
joplin-md-sync push --root ./notes --dry-run --json
joplin-md-sync push --root ./notes --json
joplin-md-sync status --root ./notes --json
```

When state is uncertain, stop writes and inspect it:

```bash
joplin-md-sync diff --root ./notes --three-way --json
joplin-md-sync conflicts list --root ./notes --json
```

Detailed tasks are in [Agent workflows](docs/user/AGENT_WORKFLOWS.md). Command
and file reference material is in [CLI reference](docs/user/CLI.md) and
[Workspace format](docs/user/WORKSPACE_FORMAT.md). Exact behavior and evidence
are in the [CLI](docs/contracts/CLI.md),
[workspace](docs/contracts/WORKSPACE.md), and
[synchronization](docs/contracts/SYNCHRONIZATION.md) contracts.

## Agent interfaces

Use the Markdown workspace for broad, reviewable transformations. Use MCP for
immediate structured Joplin operations. Use authenticated Actions for a private
Custom GPT. Choose deliberately with
[Choose an agent interface](docs/user/AGENT_INTERFACES.md).

The foreground server listens on `http://127.0.0.1:8765/mcp` by default and
does not need a Markdown workspace:

```bash
joplin-md-sync mcp serve --token-file /protected/joplin-token
```

Setup and operation:

- [MCP API](docs/user/MCP_API.md)
- [ChatGPT Actions](docs/user/CHATGPT_ACTIONS.md)
- [Self-hosted deployment](docs/user/SELF_HOSTED.md)
- [Service operations](docs/user/SERVICE.md)

The normative transport and deployment requirements are in the
[agent-interface](docs/contracts/AGENT_INTERFACES.md),
[service](docs/contracts/SERVICE.md), and
[security](docs/contracts/SECURITY.md) contracts.

## Conflict and recovery procedure

Use only the supported conflict commands; never delete or edit bundle files:

```bash
joplin-md-sync conflicts list --root ./notes --json
joplin-md-sync conflicts show CONFLICT_ID --root ./notes --json
joplin-md-sync conflicts resolve CONFLICT_ID --take-local
joplin-md-sync conflicts resolve CONFLICT_ID --take-remote
joplin-md-sync conflicts resolve CONFLICT_ID --merged-file PATH
joplin-md-sync conflicts discard CONFLICT_ID
```

If a mutating run was interrupted, use the recovery command before retrying:

```bash
joplin-md-sync recover --root ./notes --json
```

Review [Conflict handling](docs/user/CONFLICTS.md) before selecting a side.

## Safety prohibitions

- Never edit, delete, or commit anything under `.joplin-sync/`.
- Never remove or hand-edit the `id` in an existing metadata header.
- Pull before editing; review `diff` and `push --dry-run` before pushing.
- Never resolve a conflict by manipulating its bundle directly.
- Never access Joplin's database, profile, or sync target directly.
- Never put Joplin, MCP, Actions, sync, or encryption credentials in Git,
  chat, logs, or shell arguments.
- Never expose the Joplin Data API to a public network.
- Do not interleave direct MCP/Actions writes with unpushed Markdown edits.

The public documentation site is built from `docs/` with `mkdocs.yml` and is
published at <https://joplin-mcp.romancello.net/>. Documentation ownership and
evidence conventions are described in the
[contract catalog](docs/contracts/README.md).
