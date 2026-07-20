# MCP API

`joplin-md-sync mcp serve` runs a foreground
[MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
server backed by the Joplin Data API. It does not require a sync workspace and
does not read or write `.joplin-sync/`.

## Endpoint and transport

The MCP endpoint is `http://127.0.0.1:8765/mcp`. Deployment, credentials,
systemd, Task Scheduler, and live acceptance are documented once in
[Joplin API Service](SERVICE.md).

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

`GET /healthz` and `GET /readyz` return only `{"ok": true}` to loopback
clients. They are not part of either public protocol and must not be exposed by
the operator's HTTPS publishing layer.

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

## Authorization behavior

MCP bearer authentication is optional and independent of the required Actions
credential. When configured, the token file is re-read for every request and
clients send `Authorization: Bearer <secret>`. Missing or invalid credentials
return `401`. Browser-originated requests are also checked against the allowed
Origin set. See [Joplin API Service](SERVICE.md) for secure token creation,
listener binding, and operational commands.
