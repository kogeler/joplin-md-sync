# Choose an agent interface

This decision guide is non-normative. The shared operation, transport,
authentication, and limit requirements are in the
[agent-interface contract](../contracts/AGENT_INTERFACES.md).

`joplin-md-sync` exposes three client-facing surfaces backed by two distinct
consistency models.

## Decision guide

| Need | Use |
| --- | --- |
| Let a coding agent rewrite notes using repository context | Markdown workspace |
| See an exact diff and operation plan before Joplin changes | Markdown workspace |
| Keep selected notebooks in Git | Markdown workspace |
| Search current Joplin notes from an assistant | MCP |
| Perform targeted note, notebook, tag, or attachment operations | MCP |
| Connect a private Custom GPT over HTTPS | ChatGPT Actions |
| Run without a local desktop session | Headless Joplin service plus MCP or Actions |

## Markdown workspace

The workspace sync engine maintains a base snapshot and compares:

- **base**: the last verified synchronized state;
- **local**: managed Markdown files; and
- **remote**: current Joplin state.

It is the strongest review workflow. Writes are deferred until `push`, and a
dry-run shows the exact planned operations. Use it for transformations where
the agent needs files from another repository, large edits, or Git review.

[Set up the Markdown workflow](GETTING_STARTED.md)

## MCP

`joplin-md-sync mcp serve` exposes Streamable HTTP at
`http://127.0.0.1:8765/mcp`. It does not require a workspace and does not use
the base snapshot.

MCP calls operate on current Joplin state immediately. Reads retry bounded
availability failures. Writes are sent once and are never automatically
replayed after an ambiguous timeout.

Use MCP for structured discovery and narrow operations:

- list and search notes;
- read or update exact notes;
- create, rename, move, trash, and restore notes or notebooks;
- manage tags and tag relationships; and
- read, upload, replace, or traverse attachments.

[Configure an MCP client](MCP_API.md)

## ChatGPT Actions

Actions share the same registry, validation, executor, Joplin service, and HTTP
listener as MCP, but use a separate REST namespace and mandatory bearer token.
The generated OpenAPI contract exposes the operations supported by Custom GPT
Actions and excludes binary resource operations that do not fit its payload
model.

Use Actions when ChatGPT is the client and the bridge is reachable through a
controlled HTTPS hostname. Keep the Actions credential separate from the MCP
and Joplin tokens.

[Configure a private Joplin GPT](CHATGPT_ACTIONS.md)

## Consistency rule

!!! warning "Pull after direct writes"

    MCP and Actions do not update a Markdown workspace's base snapshot. After
    any direct write, run `pull` before editing local files. Do not make MCP or
    Actions changes while local edits are waiting to be pushed.

## Deployment choices

| Topology | Typical interface |
| --- | --- |
| Joplin Desktop and agent on one machine | Markdown workspace or loopback MCP |
| Joplin Desktop with a private network client | Authenticated MCP behind TLS |
| Dedicated Linux host with Joplin Terminal | Authenticated MCP and/or Actions |
| Private Custom GPT | Public HTTPS Actions namespace; Joplin API stays private |

See [Self-hosted deployment](SELF_HOSTED.md) for the trust boundaries behind
these topologies.
