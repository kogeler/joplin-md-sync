# Joplin MCP for Claude and ChatGPT

<!-- mcp-name: io.github.kogeler/joplin-md-sync -->

`joplin-md-sync` gives AI assistants controlled access to your own
[Joplin](https://joplinapp.org/) knowledge base. Search, read, create, update,
move, tag, and trash notes from hosted Claude with a GitHub login, a private
Custom GPT, or any local stdio or Streamable HTTP MCP client without moving
your notes into another hosted note service.

- **Built-in OAuth for hosted Claude.** Claude on claude.ai connects straight
  to Joplin on your own server through your GitHub login. OAuth is part of the
  server itself, so there is no separate OAuth proxy or gateway to deploy and
  configure, and the Joplin Data API stays on loopback.
- **ChatGPT today.** A private Custom GPT works through authenticated Actions.
  OpenAI retires Custom GPTs on 2026-12-11; a ChatGPT MCP connector through
  the same built-in OAuth is planned as the replacement.
- **One installer for the whole stack.** A single command sets up headless
  Joplin Terminal, recurrent sync, MCP, Actions, and Claude OAuth as rootless
  systemd services. It asks for the values it needs, hides secrets, generates
  keys and tokens, and verifies the result.

Run it beside Joplin Desktop, or deploy the complete headless stack on your own
Linux host. A separate Markdown sync workflow is available when a coding agent
needs reviewable files, three-way diffs, and Git history.

**[Website](https://joplin-mcp.romancello.net/)** ·
**[Connect Claude](https://joplin-mcp.romancello.net/user/CLAUDE_CONNECTOR/)** ·
**[Connect ChatGPT](https://joplin-mcp.romancello.net/user/CHATGPT_ACTIONS/)** ·
**[MCP: local stdio & HTTP](https://joplin-mcp.romancello.net/user/MCP_API/)** ·
**[Deploy headless](https://joplin-mcp.romancello.net/user/JOPLIN_HEADLESS_SERVICE/)**

## What you get

- **Claude with GitHub sign-in.** The built-in `/oauth/mcp` interface lets
  hosted Claude, currently its only supported OAuth client, sign in with one
  configured numeric GitHub account on the existing service port. The
  installer configures it from a few answers; CLI flags and private
  secret-file options do the same by hand.
- **Joplin inside ChatGPT.** A generated OpenAPI contract exposes authenticated
  Actions for current notes, notebooks, tags, and search to a private Custom
  GPT.
- **Typed MCP tools.** Local stdio and Streamable HTTP transports give
  compatible clients structured note operations plus attachment upload,
  download, replacement, and relationship traversal.
- **A headless Joplin service.** The rootless Linux installer deploys Joplin
  Terminal, recurrent sync, and the combined MCP, OAuth, and Actions adapter
  as coordinated systemd user services. Joplin Desktop does not need to remain
  running.
- **Your storage and encryption choices.** Use filesystem, Nextcloud, WebDAV,
  Dropbox, OneDrive, S3, Joplin Server, or Joplin Cloud, with existing Joplin
  end-to-end encryption where the selected topology supports it.
- **Reviewable Markdown when you need it.** Pull notebooks to ordinary files,
  let an agent work with repository context, inspect a three-way diff and
  dry-run, then push the verified result back to Joplin.

Joplin remains the source of truth. The adapter uses its documented local
[Data API](https://joplinapp.org/help/api/references/rest_api/) and never edits
the Joplin database, profile, or sync target directly.

Joplin Desktop also ships its own MCP server for clients on the same computer.
Use this project when the assistant runs elsewhere, such as hosted Claude or
ChatGPT, when Joplin runs headless on a server, or when you need attachments
and reviewable Markdown changes. See the
[comparison](https://joplin-mcp.romancello.net/#built-in-mcp).

## Fast path: headless Joplin for Claude and ChatGPT

This topology keeps Joplin and the adapter on your own Linux host. Only the
authenticated OAuth or Actions routes need to reach the assistant over HTTPS;
the Joplin Data API stays on loopback.

### 1. Install Joplin and the agent service

The host needs Linux with systemd user services, Python 3.13.5 or newer, and
Node.js/npm. This interactive example connects the new headless profile to an
existing Nextcloud Joplin sync target:

```bash
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  | python3 - \
      --sync-target nextcloud \
      --sync-location 'https://cloud.example.com/remote.php/dav/files/user/Joplin' \
      --sync-username 'user'
```

The installer asks for secrets through hidden prompts, offers GitHub OAuth for
Claude, handles existing Joplin E2EE keys, verifies release checksums, and
creates:

```text
joplin-terminal.service    Joplin profile, Data API, and recurrent sync
joplin-md-sync.service     MCP, Claude OAuth, and ChatGPT Actions on one guarded adapter
```

It also generates separate protected credentials for Joplin, MCP, and Actions,
plus the OAuth signing key when OAuth is enabled.
For ready-to-run Nextcloud, custom S3, and uninstall commands, see
[Joplin Headless Service](https://joplin-mcp.romancello.net/user/JOPLIN_HEADLESS_SERVICE/).
The guide also explains installed paths, every installer option, and upgrades.

### 2. Publish the narrow HTTPS routes

From a trusted HTTPS hostname, route only what your assistant needs to the
adapter on `127.0.0.1:8765`: the OAuth routes for Claude, `/api/gpt/v1/*` for
ChatGPT, or both. Do not expose the upstream Joplin Data API. Keep `/mcp`
private unless a remote MCP client needs it; public MCP requires its own bearer
token and TLS.

The supported boundaries and deployment choices are documented in
[Self-hosted deployment](https://joplin-mcp.romancello.net/user/SELF_HOSTED/).

### 3. Connect Claude

Register a GitHub OAuth App, then answer yes to the installer's
`Enable GitHub OAuth for Claude?` question or rerun it with `--github-oauth`.
It asks for the public HTTPS origin, the app's Client ID and Client Secret, and
your numeric GitHub user ID, then enables OAuth on the running service and
checks it. Add `https://YOUR_HOST/oauth/mcp` as a custom connector in Claude
and sign in with GitHub. The exact routes, app settings, and checks are in the
[Claude connector guide](https://joplin-mcp.romancello.net/user/CLAUDE_CONNECTOR/).

### 4. Connect a private Custom GPT

From a checkout matching the deployed release, run the setup assistant:

```bash
git clone --depth 1 --branch v1.8.1 \
  https://github.com/kogeler/joplin-md-sync.git
cd joplin-md-sync
python3 scripts/prepare_chatgpt_action.py
```

Enter the public hostname and the generated Actions token when prompted. The
assistant validates TLS, authentication, and live read operations, then writes
the OpenAPI file for the Custom GPT editor. Continue with the exact GPT
instructions and acceptance test in
[ChatGPT Actions setup](https://joplin-mcp.romancello.net/user/CHATGPT_ACTIONS/).

## Use Joplin from an MCP client

For a local Joplin Desktop instance, enable **Tools > Options > Web Clipper**
and point an MCP-capable editor at the downloaded native executable:

```json
{
  "mcpServers": {
    "joplin": {
      "command": "/absolute/path/to/joplin-md-sync-linux-amd64",
      "args": ["mcp", "stdio", "--token-file", "/home/USER/.config/joplin-md-sync/token"]
    }
  }
}
```

Store the token once in a private file outside synchronized folders. The
directory is `0700`, the file `0600`, and the token is read without echo or
shell history:

```bash
install -d -m 0700 ~/.config/joplin-md-sync
(umask 077; read -rs -p 'Joplin token: ' token &&
  printf '%s\n' "$token" > ~/.config/joplin-md-sync/token)
```

A launcher that exports environment variables can set `JOPLIN_TOKEN` instead
and pass neither token option. Use exactly one source: a command-line option
together with a non-empty `JOPLIN_TOKEN` is rejected. The older
`"--token", "<token>"` arguments still work for existing configurations, but
they place the token in the IDE configuration, in the process list, and in any
agent command line that forwards the MCP configuration.

The local stdio process connects to Joplin on port `41184` by default; add
`"--port", "PORT"` to the arguments when Joplin uses another local port. For
a long-running or remote adapter, install the package and start Streamable HTTP
instead. Stdio opens no MCP or Actions port and therefore needs no bearer token
for those interfaces; its Joplin token authenticates only to Joplin:

```bash
pipx install "joplin-md-sync==1.8.1"
export JOPLIN_TOKEN=...
joplin-md-sync mcp serve
```

Connect the HTTP client to `http://127.0.0.1:8765/mcp` with transport type
`streamable-http`.

The service can start while Joplin is offline and recovers on later calls.
Create operations reject an existing notebook, note, tag, or resource identity
instead of creating accidental duplicates. Remote MCP deployment requires a
separate protected bearer token and TLS. See the complete
[MCP API reference](https://joplin-mcp.romancello.net/user/MCP_API/).

## Use Joplin notes as reviewable files

Choose the Markdown workflow when an agent needs repository context, broad
transformations, an exact diff before writes, or selected notebooks in Git:

```bash
export JOPLIN_TOKEN=...
joplin-md-sync init --root ./notes --mode remote-first
joplin-md-sync pull --root ./notes --json

# Let an agent edit the managed Markdown files, then review the result.
joplin-md-sync diff --root ./notes --three-way --unified
joplin-md-sync push --root ./notes --dry-run --json
joplin-md-sync push --root ./notes --json
```

Managed note files carry a one-line Joplin identity header. Base snapshots,
conflicts, journals, backups, and downloaded resources stay under the ignored
`.joplin-sync/` directory. Start with the copyable
[agent notes repository template](https://github.com/kogeler/joplin-md-sync/tree/main/examples/agent-notes-repository)
or the
[Markdown quick start](https://joplin-mcp.romancello.net/user/GETTING_STARTED/).

## Control and failure behavior

- The Joplin Data API remains private on loopback in the headless topology.
- Joplin, MCP, and Actions credentials are distinct and read from protected
  files or the environment. Local `mcp stdio` takes its Joplin token from a
  protected `--token-file` or `JOPLIN_TOKEN`; the compatibility `--token`
  argument exposes it in the process list.
- Direct API writes are sent once. An ambiguous timeout is reported instead of
  being replayed and possibly duplicated.
- Creating an occupied notebook, note, tag, or resource identity returns an
  explicit `*_ALREADY_EXISTS` error with the existing ID and recommended update
  tool.
- Note and notebook deletion uses Joplin trash. Resource and tag deletion is
  explicitly marked destructive because Joplin has no trash endpoint for them.
- Divergent file edits produce a conflict bundle instead of a silent overwrite.
- File deletion propagation is off by default; interrupted mutations are
  journaled and recoverable; `diff` never mutates state.

The exact test-backed guarantees live in the
[contract catalog](https://joplin-mcp.romancello.net/contracts/). Agents
operating a Markdown workspace should also follow
[AGENTS.md](https://github.com/kogeler/joplin-md-sync/blob/main/AGENTS.md).

## Installation options

Python installations require CPython 3.13 or 3.14 on Windows or Linux:

```bash
python -m pip install "joplin-md-sync==1.8.1"
# or: pipx install "joplin-md-sync==1.8.1"
```

Native executables from GitHub Releases include Python and the standard service
runtime. A Python zipapp is also available for a prepared Python environment:

| Platform | Asset |
| --- | --- |
| Linux AMD64 | `joplin-md-sync-linux-amd64` |
| Linux ARM64 | `joplin-md-sync-linux-arm64` |
| Windows AMD64 | `joplin-md-sync-windows-amd64.exe` |
| Python zipapp | `joplin-md-sync.pyz` |

Download them with `SHA256SUMS.txt` from the
[latest release](https://github.com/kogeler/joplin-md-sync/releases/latest).

## Interface guide

| Goal | Interface |
| --- | --- |
| Connect hosted Claude with GitHub sign-in | Built-in OAuth MCP at `/oauth/mcp` |
| Work with current Joplin notes in ChatGPT | Authenticated ChatGPT Actions |
| Give an editor or assistant typed note tools | MCP |
| Run without a desktop session | Headless Joplin Terminal plus MCP, OAuth, or Actions |
| Review every broad agent edit before applying it | Markdown workspace |
| Keep selected notebooks in Git | Markdown workspace |

Compare consistency models and deployment choices in
[Choose an agent interface](https://joplin-mcp.romancello.net/user/AGENT_INTERFACES/).

## Development

```bash
make venv
make venv-dev
make check
make ci
make test-live
make package
make docs-audit
make docs-screenshots
make help
```

The standard installation includes the HTTP and GitHub OAuth runtime; CLI flags
enable the interfaces. Development, test, package, and docs tools use separate
dependency groups from the single hash-verified `uv.lock`. See
[Development](https://joplin-mcp.romancello.net/maintenance/DEVELOPMENT/) and
[Dependency maintenance](https://joplin-mcp.romancello.net/maintenance/DEPENDENCIES/).
On Linux AMD64, `make test-live` provisions a checksum-pinned Joplin Desktop
3.6.15 binary and isolated temporary profile; it never uses the user's Joplin
process or token.

## Releases and license

Git tags use `vX.Y.Z`. Release assets include wheel, sdist, zipapp, native
executables, and checksums; the same wheel and sdist are published to PyPI
through Trusted Publishing. See the
[changelog](https://github.com/kogeler/joplin-md-sync/blob/main/CHANGELOG.md).

[MIT licensed](https://github.com/kogeler/joplin-md-sync/blob/main/LICENSE).
This is an independent project and is not affiliated with or endorsed by the
Joplin project.
