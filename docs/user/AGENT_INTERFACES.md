# Choose an agent interface

This decision guide is non-normative. The shared operation, transport,
authentication, and limit requirements are in the
[agent-interface contract](../contracts/AGENT_INTERFACES.md).

`joplin-md-sync` exposes three client-facing surfaces backed by two distinct
consistency models.

## Decision guide

| Need | Use |
| --- | --- |
| Work with current Joplin notes in a private Custom GPT | ChatGPT Actions |
| Search or change current Joplin data from an assistant | MCP |
| Perform targeted note, notebook, tag, or attachment operations | MCP |
| Run without a local desktop session | Headless Joplin service plus MCP or Actions |
| Let a coding agent rewrite notes using repository context | Markdown workspace |
| See an exact diff and operation plan before Joplin changes | Markdown workspace |
| Keep selected notebooks in Git | Markdown workspace |

## ChatGPT Actions

Actions use a dedicated REST namespace and mandatory bearer token on the same
listener and operation registry as MCP. The generated OpenAPI contract exposes
the operations supported by Custom GPT Actions and excludes binary resource
operations that do not fit its payload model.

Use Actions when ChatGPT is the client and the bridge is reachable through a
controlled HTTPS hostname. Keep the Actions credential separate from the MCP
and Joplin tokens. A private Custom GPT can search, read, create, update, move,
tag, trash, and restore current Joplin objects without a Markdown workspace.

[Configure a private Joplin GPT](CHATGPT_ACTIONS.md)

## MCP

`joplin-md-sync mcp stdio --token TOKEN` lets a local editor launch the native
executable directly, while `joplin-md-sync mcp serve` exposes Streamable HTTP
at `http://127.0.0.1:8765/mcp`. Neither requires a workspace or uses the base
snapshot.

The stdio process opens no listener and needs no MCP or Actions bearer token;
its required `--token` is only the upstream Joplin Web Clipper credential.
`mcp serve` retains the existing HTTP, authorization, Origin, and optional
Actions behavior.

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

## Markdown workspace

The workspace sync engine maintains a base snapshot and compares:

- **base**: the last verified synchronized state;
- **local**: managed Markdown files; and
- **remote**: current Joplin state.

It is the strongest review workflow. Writes are deferred until `push`, and a
dry-run shows the exact planned operations. Use it for transformations where
the agent needs files from another repository, large edits, or Git review.

[Set up the Markdown workflow](GETTING_STARTED.md)

## Consistency rule

!!! warning "Pull after direct writes"

    MCP and Actions do not update a Markdown workspace's base snapshot. After
    any direct write, run `pull` before editing local files. Do not make MCP or
    Actions changes while local edits are waiting to be pushed.

## Deployment choices

| Topology | Typical interface |
| --- | --- |
| Private Custom GPT | Public HTTPS Actions namespace; Joplin API stays private |
| Dedicated Linux host with Joplin Terminal | Authenticated MCP and/or Actions |
| Joplin Desktop with a private network client | Authenticated MCP behind TLS |
| Joplin Desktop and agent on one machine | Local stdio MCP or Markdown workspace |

See [Self-hosted deployment](SELF_HOSTED.md) for the trust boundaries behind
these topologies.
