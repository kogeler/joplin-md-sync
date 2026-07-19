# MCP server

`joplin-md-sync mcp serve` runs a foreground
[MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
server backed by the Joplin Data API. It does not require a sync workspace and
does not read or write `.joplin-sync/`.

## Start

```bash
export JOPLIN_TOKEN=...                  # upstream Joplin Data API token
joplin-md-sync mcp serve --verbose
```

The MCP endpoint is `http://127.0.0.1:8765/mcp`. The server runs in the
foreground; use systemd, Task Scheduler, or another supervisor for autostart
and restart behavior.

Example MCP client settings without MCP authorization:

```json
{
  "type": "streamable-http",
  "url": "http://127.0.0.1:8765/mcp"
}
```

Joplin is resolved with the normal connection precedence. `--base-url`,
`--port`, `--token-file`, `--timeout`, and `--allow-remote-api` configure the
upstream Joplin connection. They do not configure the MCP listener.

## Tools

| Tool | Behavior |
| --- | --- |
| `joplin_list_notebooks` | List active or trashed notebooks and parent relationships |
| `joplin_get_notebook` | Read notebook metadata and trash state |
| `joplin_create_notebook` | Create a root or nested notebook |
| `joplin_update_notebook` | Rename, move, or update notebook metadata |
| `joplin_delete_notebook` / `joplin_restore_notebook` | Move to trash or restore; never permanently delete |
| `joplin_list_notebook_notes` | List notes directly contained in a notebook |
| `joplin_list_notes` | List note IDs and core metadata, without bodies |
| `joplin_get_note` | Read Markdown body, tags, notebook, timestamps, todo/source metadata |
| `joplin_create_note` | Create Markdown/HTML notes with metadata, tags, and base64 attachments |
| `joplin_update_note` | Partially update content/metadata; supplied tags replace the tag set |
| `joplin_delete_note` / `joplin_restore_note` | Move a note to trash or restore it |
| `joplin_search_notes` | Run the normal Joplin full-text search syntax |
| `joplin_list_tags` / `joplin_get_tag` | List or read tag metadata |
| `joplin_create_tag` / `joplin_update_tag` | Create or rename a tag |
| `joplin_delete_tag` | Permanently delete a tag and its note associations |
| `joplin_list_tag_notes` | List notes associated with a tag |
| `joplin_add_tag_to_note` / `joplin_remove_tag_from_note` | Change one tag relation without replacing other tags |
| `joplin_list_resources` / `joplin_get_resource` | List or read attachment metadata |
| `joplin_read_resource` | Return attachment content as base64, up to 10 MiB decoded |
| `joplin_create_resource` / `joplin_update_resource` | Multipart upload or replace binary content and metadata |
| `joplin_delete_resource` | Permanently delete an attachment |
| `joplin_list_note_resources` / `joplin_list_resource_notes` | Traverse note-resource relationships in either direction |

List and search results accept a bounded `limit` (maximum 100). Use
`joplin_list_notebooks` before create/move operations to obtain `parent_id`.
Tool results contain both JSON text and MCP `structuredContent`.

`joplin_create_note` accepts either an existing `parent_id` or a
`notebook_title`. With `notebook_title`, the tool reuses an exact title match or
creates that notebook. If neither is supplied, it similarly finds or creates
`MCP Notes`. Supplying an unknown `parent_id` returns `NOTEBOOK_NOT_FOUND` and
does not attempt the note write.

The note body can be supplied as `body` (Joplin Markdown, which may include
HTML) or `body_html` with an optional `base_url`. Joplin's native
`image_data_url` and `crop_rect` inputs are also exposed. The `attachments`
array accepts `filename`, `mime`, optional title/alt text, and
`content_base64`; each decoded item is limited to 10 MiB. Uploaded images are
appended as `![alt](:/resource-id)` and other files as
`[label](:/resource-id)`.

The HTTP request limit is 16 MiB, including JSON/base64 overhead. Larger files
must be split outside MCP or uploaded by another Joplin client. Resource and
tag deletion are permanent because Joplin does not provide trash endpoints for
those object types; their MCP tools carry `destructiveHint: true`. Note and
notebook deletion remains trash-only and can be reversed with restore tools.

Some Joplin Desktop versions return an empty result for the documented reverse
resource-to-notes endpoint. `joplin_list_resource_notes` falls back to scanning
the exact `:/resource-id` links in note bodies when that happens.

## Availability

The process does not contact Joplin while binding the MCP port. If Joplin is
off, restarting, or temporarily unreachable, each tool call waits for the
bounded `--retry-timeout` (10 seconds by default) and uses retrying reads. A
failure is returned as an MCP tool result rather than terminating the server:

```json
{
  "error": {
    "code": "API_UNAVAILABLE",
    "message": "Joplin API unreachable ...",
    "retryable": true
  }
}
```

Later calls retry and succeed once Joplin returns. Writes perform a retrying
availability preflight but the mutation itself is sent once: automatically
replaying an ambiguous create/update/delete could duplicate or overwrite data.
An ambiguous write returns `AMBIGUOUS_WRITE` with `retryable: false`; inspect
the note before deciding whether to repeat it.

Relevant tuning options:

```text
--timeout SECONDS             timeout for one Joplin HTTP attempt (MCP default 5)
--retry-timeout SECONDS       total bounded availability wait (default 10)
--retry-delay SECONDS         delay between availability attempts (default 1)
--discovery-timeout SECONDS   timeout for each discovery port (default 0.25)
```

## MCP authorization

MCP authorization is disabled by default. To require a pre-shared bearer token,
put a separate secret in a protected file and pass `--auth-token-file`:

```bash
umask 077
python -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.config/joplin-md-sync/mcp-token
joplin-md-sync mcp serve \
  --auth-token-file ~/.config/joplin-md-sync/mcp-token
```

Clients must then send `Authorization: Bearer <secret>` on every request. The
file is re-read for each request, so replacing it rotates the token without a
server restart. This secret is independent of the upstream Joplin token and is
never forwarded to Joplin.

This mode is deliberately a small pre-shared bearer check, not an OAuth 2.1
authorization server or discovery flow. Keep the default loopback bind for
local clients. A non-loopback bind is rejected unless both
`--allow-remote-mcp` and `--auth-token-file` are present; when accessing MCP
over a network, terminate TLS in a trusted reverse proxy and restrict network
access as well.

Browser-originated requests are checked against the listener. Loopback origins
are accepted on a loopback listener; add exact origins with repeatable
`--allowed-origin https://host.example` when a trusted web client requires it.

## Linux systemd user service

The rootless Python installer documented in
[Headless Joplin Terminal and MCP services](joplin-terminal-service.md)
downloads and verifies a released Linux standalone executable, generates the
Joplin and MCP user units from one API-port setting, configures optional MCP
bearer authentication and opt-in `0.0.0.0` binding, checks user lingering, and
verifies both services. It supersedes the former static example unit.

## Windows autostart

The example [PowerShell installer](../examples/windows/install-mcp-task.ps1)
registers a per-user Task Scheduler task at logon and configures restart on
failure:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\joplin-md-sync"
Set-Content "$env:APPDATA\joplin-md-sync\joplin-token" "<Joplin token>"
Set-Content "$env:APPDATA\joplin-md-sync\mcp-token" "<separate MCP secret>"
.\examples\windows\install-mcp-task.ps1 `
  -Executable "$env:USERPROFILE\.local\bin\joplin-md-sync.exe" `
  -JoplinTokenFile "$env:APPDATA\joplin-md-sync\joplin-token" `
  -McpAuthTokenFile "$env:APPDATA\joplin-md-sync\mcp-token"
```

Use Windows ACLs to restrict both files to the current account. Run
`Unregister-ScheduledTask -TaskName joplin-md-sync-mcp -Confirm:$false` to
remove the task.

## Live acceptance tests

The real-Joplin suite is deliberately outside `tests/`, so `make test`,
`make check`, and CI never collect it. From a checkout, place the Joplin token
in the ignored repository-root `token` file and run:

```bash
chmod 600 token
make test-live
```

The suite launches the real CLI MCP daemon and checks initialization, all note
tools, notebook creation/selection, metadata and tag replacement, note moves,
Joplin full-text search, trash semantics, authorization, Origin handling, and
unavailable-upstream errors.

Every test object name contains a random UUID. Update and delete helpers refuse
IDs not returned by the current run. Teardown discovers partial writes by that
UUID, excludes every pre-existing ID, permanently removes only allowlisted test
notes/tags/notebooks, and verifies that the original note update/deletion state
did not change.
