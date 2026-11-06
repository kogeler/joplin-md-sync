# joplin-md-sync Agent Runbook

Safe two-way synchronization between Joplin and ordinary Markdown, with local
stdio and HTTP MCP plus ChatGPT Actions interfaces for agents. Product
guarantees are defined only by the [contract catalog](docs/contracts/README.md).
This runbook tells an agent how to operate the project safely; it does not
replace those contracts.

## Agent authorization and project language

### English-only project material

- Write all project-authored material in English: documentation, agent
  instructions, working plans, code comments, docstrings, user-facing messages,
  test descriptions, commit messages, and pull request titles or other manually
  authored repository metadata. The language of the conversation does not
  change this rule.
- Keep working plans, research notes, task trackers, and review journals in the
  Git-ignored `tmp/` directory. Do not add them to Git, `docs/`, site navigation,
  or published documentation links unless the user explicitly requests
  publication of that specific material. Extract durable guidance into the
  appropriate maintained English guide instead.
- Preserve user-owned content and multilingual test payloads as data. This
  language rule does not authorize translating user notes or removing Unicode
  coverage. Use the user's preferred language in conversation.

### Explicit authorization for commits and pushes

- Editing, reviewing, testing, fixing, or completing a task does not authorize
  a commit or push. Leave changes uncommitted unless the user explicitly asks
  for a commit. Do not create commits automatically to record progress.
- Never push without an explicit user instruction authorizing that push or a
  clearly defined batch of pushes. Authorization to commit does not authorize
  a push. Authorization for a different change, branch, or task does not carry
  over.
- This restriction covers every remote, branch, tag, force push, and equivalent
  remote Git-ref update through APIs, web interfaces, or helper scripts.
- Do not push to trigger CI, update a pull request, publish documentation, or
  obtain remote validation without the required instruction. Run appropriate
  local checks and report the local result instead.
- Do not rewrite history, revert published commits, merge, or publish a release
  as a substitute for an unauthorized push. Such actions require their own
  explicit user instruction.

## Requirements

- CPython 3.13 or 3.14 on Windows or Linux for source, wheel, and zipapp use.
- Joplin Desktop running locally with Web Clipper enabled, normally on port
  `41184`.
- The Joplin Web Clipper token, supplied through exactly one of `JOPLIN_TOKEN`
  or a private token file (mode `0600`, or an owner-only Windows ACL). Local
  `mcp stdio` also keeps a compatibility `--token` argument.

Native release executables include Python.

## Install

```bash
python -m pip install "joplin-md-sync==1.8.0"
# or:
pipx install "joplin-md-sync==1.8.0"
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
make lock-check
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

Make keeps every environment under `.venvs/<key>/`, private to the current
machine and user, so one checkout on a shared or network drive works from
several hosts. Development dependencies live in standard `pyproject.toml`
dependency groups; uv and Dependabot maintain the single `uv.lock`, which is
never hand-edited. Install uv before using Make; CI selects its version from
`.tool-versions`. Use `make lock` after editing requirements and `make lock-check`
to validate them without rewriting the lock. Ordinary changes add notes under
`## [Unreleased]` in `CHANGELOG.md` and keep `.version`; only deliberate
release preparation advances it.

### Pull request descriptions

Never manually write or edit a pull request body, including text outside the
CI-managed changelog block. Do not update it through `gh pr edit`, REST,
GraphQL, scripts, or any other tool. When creating a PR, leave its body empty
(`gh pr create --body ""`); do not use `--fill` or generate a description from
commits.

CI owns description updates through `.github/workflows/pr-body.yml`, using
`CHANGELOG.md` as the source. To change the generated description, update and
verify the appropriate changelog section (normally `## [Unreleased]`) locally.
Commit and push only when explicitly authorized under the rules above; CI then
updates the PR. Never copy changelog text into the body yourself or edit the
managed markers.

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
Custom GPT and the built-in GitHub OAuth interface for hosted Claude. Choose
deliberately with
[Choose an agent interface](docs/user/AGENT_INTERFACES.md).

The foreground server listens on `http://127.0.0.1:8765/mcp` by default and
does not need a Markdown workspace:

```bash
joplin-md-sync mcp serve --token-file /protected/joplin-token
```

A local IDE can instead launch the release executable over stdio while Joplin
Desktop is already running. Configure its process argument vector with
`mcp stdio --token-file /absolute/path/to/joplin-token`, pointing at a private
`0600` file, or pass `mcp stdio` alone and export `JOPLIN_TOKEN` to the process;
use exactly one of the two, because both together are rejected. Add
`--port PORT` only when Joplin does not use the default `41184`. The older
`mcp stdio --token TOKEN` form still works but exposes the token in the IDE
configuration and the process list. This mode opens no MCP, Actions, health, or
readiness port and needs no bearer credential for those interfaces.

Setup and operation:

- [MCP API](docs/user/MCP_API.md)
- [ChatGPT Actions](docs/user/CHATGPT_ACTIONS.md)
- [Claude connector](docs/user/CLAUDE_CONNECTOR.md)
- [Self-hosted deployment](docs/user/SELF_HOSTED.md)
- [Joplin Headless Service](docs/user/JOPLIN_HEADLESS_SERVICE.md)

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
- Never put Joplin, MCP, Actions, GitHub OAuth, sync, or encryption
  credentials in Git, chat, logs, or process arguments. Configure local
  `mcp stdio` with `--token-file` or `JOPLIN_TOKEN`; its `--token` form exists
  only for older IDE configurations.
- Never expose the Joplin Data API to a public network.
- Do not interleave direct MCP/Actions writes with unpushed Markdown edits.

The public documentation site is built from `docs/` with `mkdocs.yml` and is
published at <https://joplin-mcp.romancello.net/>. Documentation ownership and
evidence conventions are described in the
[contract catalog](docs/contracts/README.md).
