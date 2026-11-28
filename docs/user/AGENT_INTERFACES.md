---
title: "Choose how AI agents reach Joplin: Claude, MCP or Markdown"
description: "Compare the Claude connector with built-in OAuth, local stdio MCP, HTTP MCP and Markdown sync for giving an AI agent your Joplin notes."
---

# Choose an agent interface

This decision guide is non-normative. The shared operation, transport,
authentication, and limit requirements are in the
[agent-interface contract](../contracts/AGENT_INTERFACES.md).

`joplin-md-sync` exposes three client-facing surfaces backed by two distinct
consistency models: the Claude connector, MCP, and the Markdown workspace.

## Decision guide

| Need | Use |
| --- | --- |
| Connect hosted Claude with one configured GitHub account | [Built-in OAuth MCP](CLAUDE_CONNECTOR.md) |
| Search or change current Joplin data from an assistant | MCP |
| Perform targeted note, notebook, tag, or attachment operations | MCP |
| Run without a local desktop session | Headless Joplin service plus MCP or OAuth |
| Let a coding agent rewrite notes using repository context | Markdown workspace |
| See an exact diff and operation plan before Joplin changes | Markdown workspace |
| Keep selected notebooks in Git | Markdown workspace |

Joplin Desktop also has a built-in MCP server on its Web Clipper port. It
serves only clients on the same computer while the desktop app runs, and it
adds semantic search. Choose this project when an assistant runs elsewhere,
such as hosted Claude, when Joplin runs headless on a server, or when you need
attachments or reviewable Markdown changes.

## Claude connector

Hosted Claude connects to `/oauth/mcp` on the same listener and signs in
through GitHub. OAuth is built into the server: it issues and checks its own
tokens, limits access to one numeric GitHub account, and needs no separate
OAuth proxy or identity gateway in front of the MCP endpoint. The headless
installer configures it from a few answers. It is currently the only supported
OAuth client.

[Connect Claude to Joplin](CLAUDE_CONNECTOR.md)

## MCP

`joplin-md-sync mcp stdio --token-file PATH` lets a local editor launch the
native executable directly, while `joplin-md-sync mcp serve` exposes Streamable
HTTP at `http://127.0.0.1:8765/mcp`. Neither requires a workspace or uses the
base snapshot.

The stdio process opens no listener and needs no MCP bearer token; it only
needs the upstream Joplin Web Clipper token, from a protected `--token-file` or
from `JOPLIN_TOKEN`, never both. The compatibility `--token` argument still
works but exposes the token in the process list. `mcp serve` retains the
existing HTTP, authorization, and Origin behavior.

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

    MCP does not update a Markdown workspace's base snapshot. After any direct
    write, run `pull` before editing local files. Do not make MCP changes while
    local edits are waiting to be pushed.

## Deployment choices

| Topology | Typical interface |
| --- | --- |
| Hosted Claude | Public HTTPS OAuth routes on the same listener; Joplin API stays private |
| Dedicated Linux host with Joplin Terminal | Authenticated MCP and/or OAuth |
| Joplin Desktop with a private network client | Authenticated MCP behind TLS |
| Joplin Desktop and agent on one machine | Local stdio MCP or Markdown workspace |

See [Self-hosted deployment](SELF_HOSTED.md) for the trust boundaries behind
these topologies.
