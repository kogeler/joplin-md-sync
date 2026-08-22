# MCP API

This is the client-facing tool reference. Its normative registry, lifecycle,
failure, authentication, and resource-limit behavior is defined by the
[agent-interface contract](../contracts/AGENT_INTERFACES.md).

`joplin-md-sync` exposes MCP through local stdio or foreground
[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
Both transports use the same tools and Joplin Data API service. Neither needs a
sync workspace or reads or writes `.joplin-sync/`.

## Endpoint and transport

### Local stdio

Use stdio when the MCP client and Joplin Desktop run on the same machine. Give
the IDE the native executable path and these arguments:

```json
{
  "mcpServers": {
    "joplin": {
      "command": "/absolute/path/to/joplin-md-sync-linux-amd64",
      "args": ["mcp", "stdio", "--token", "<Joplin Web Clipper token>"]
    }
  }
}
```

On Windows, use the absolute path to
`joplin-md-sync-windows-amd64.exe`. `--token` is mandatory. Add
`"--port", "41185"` to `args` when Joplin uses a non-default port; otherwise
the process connects to `127.0.0.1:41184`. Joplin must already be running with
Web Clipper enabled.

The IDE launches one process, writes newline-delimited JSON-RPC to stdin, and
reads JSON-RPC from stdout. Closing stdin stops the process. No TCP MCP port,
MCP bearer credential, or Markdown workspace is involved. It also exposes no
GPT Actions, health, or readiness HTTP routes, so their bearer options are not
accepted. Diagnostics remain on stderr.

The Joplin token is stored in the IDE configuration and appears in the local
process argument vector. Restrict access to that configuration and avoid
typing this form into an interactive shell. This raw-token form is supported
only by `mcp stdio`; other commands retain the token-file/environment policy.

### Streamable HTTP

`mcp serve` remains the network mode and opens the MCP endpoint at
`http://127.0.0.1:8765/mcp` by default. Deployment, credentials, systemd, Task
Scheduler, and live acceptance are documented once in [Joplin API
Service](SERVICE.md).

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

The listener also routes GPT Actions in the same process when enabled. MCP
discovery/calls and Actions operations use one immutable tool
registry, validator, executor, service, and Joplin client factory. Actions do
not issue JSON-RPC or make a loopback call to `/mcp`. Actions configuration
does not change the MCP endpoint, headers, or authentication semantics.

`GET /healthz` and `GET /readyz` return only `{"ok": true}` and no credentials
or Joplin data. They are not part of either client protocol. A publishing layer
for the whole shared listener may expose them; clients must not treat them as
an authentication check.

## Tools

| Tool | Behavior |
| --- | --- |
| `joplin_list_notebooks` | List active or trashed notebooks and parent relationships |
| `joplin_get_notebook` | Read notebook metadata and trash state |
| `joplin_create_notebook` | Create a root or nested notebook; reject an occupied sibling title |
| `joplin_update_notebook` | Rename, move, or update notebook metadata |
| `joplin_delete_notebook` / `joplin_restore_notebook` | Move to trash or restore; never permanently delete |
| `joplin_list_notebook_notes` | List notes directly contained in a notebook |
| `joplin_list_notes` | List note IDs and core metadata, without bodies |
| `joplin_get_note` | Read Markdown body, tags, notebook, timestamps, todo/source metadata |
| `joplin_create_note` | Create Markdown/HTML notes with metadata, tags, and base64 attachments; reject an occupied title in the destination notebook |
| `joplin_update_note` | Partially update content/metadata; supplied tags replace the tag set |
| `joplin_delete_note` / `joplin_restore_note` | Move a note to trash or restore it |
| `joplin_search_notes` | Run the normal Joplin full-text search syntax |
| `joplin_list_tags` / `joplin_get_tag` | List or read tag metadata |
| `joplin_create_tag` / `joplin_update_tag` | Create a unique normalized title or rename a tag |
| `joplin_delete_tag` | Permanently delete a tag and its note associations |
| `joplin_list_tag_notes` | List notes associated with a tag |
| `joplin_add_tag_to_note` / `joplin_remove_tag_from_note` | Change one tag relation without replacing other tags |
| `joplin_list_resources` / `joplin_get_resource` | List or read attachment metadata |
| `joplin_read_resource` | Return attachment content as base64, up to 10 MiB decoded |
| `joplin_create_resource` / `joplin_update_resource` | Upload a unique resource identity or replace binary content and metadata |
| `joplin_delete_resource` | Permanently delete an attachment |
| `joplin_list_note_resources` / `joplin_list_resource_notes` | Traverse note-resource relationships in either direction |

List and search results accept a bounded `limit` (maximum 100). Use
`joplin_list_notebooks` before create/move operations to obtain `parent_id`.
Tool results contain both JSON text and MCP `structuredContent`.

`joplin_create_note` accepts either an existing `parent_id` or a
`notebook_title`. With `notebook_title`, the tool reuses one normalized root
title match or creates that notebook. If neither is supplied, it similarly
finds or creates `MCP Notes`. Multiple matches return
`NOTEBOOK_PATH_AMBIGUOUS`; a matching trashed notebook returns
`NOTEBOOK_NOT_ACTIVE`. Supplying an unknown `parent_id` returns
`NOTEBOOK_NOT_FOUND` and does not attempt the note write.

## Create conflicts

Create tools refuse to create a second object with the same natural identity:

| Object | Compared identity | Error |
| --- | --- | --- |
| Notebook | parent notebook ID and normalized title | `NOTEBOOK_ALREADY_EXISTS` |
| Note | destination notebook ID and normalized title | `NOTE_ALREADY_EXISTS` |
| Tag | normalized title | `TAG_ALREADY_EXISTS` |
| Resource | normalized resource title (`filename` is the default title) | `RESOURCE_ALREADY_EXISTS` |

Name comparison trims surrounding whitespace, uses Unicode NFC, and is
case-insensitive. The check includes notes and notebooks in trash. An error
contains `details.existing_ids`, `details.existing_id` when there is one match,
and `details.recommended_tool`. It is non-retryable and causes no create-side
effect. MCP returns it as an `isError: true` tool result; the same shared error
is HTTP `409 Conflict` through GPT Actions.

Use the returned ID with the corresponding update tool. When a required
replacement cannot be expressed as an update, first rename the old object,
create and verify the replacement at the released path, and only then trash or
delete the renamed object. If an old match is already in trash, restore it long
enough to rename it before creating the replacement.

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

## Authorization behavior

MCP bearer authentication is optional and independent of the required Actions
credential. When configured, the token file is re-read for every request and
clients send `Authorization: Bearer <secret>`. Missing or invalid credentials
return `401`. The file must be a protected, current-user-owned regular file
containing one URL-safe Base64 token that encodes at least 32 bytes;
generate it with `secrets.token_urlsafe(32)`. Symlinks, weak or oversized
tokens, insecure POSIX modes, non-ASCII headers, and duplicate Authorization
headers are rejected. Browser-originated requests are also checked against the
allowed Origin set. See [Joplin API Service](SERVICE.md) for secure token
creation, listener binding, and operational commands.
