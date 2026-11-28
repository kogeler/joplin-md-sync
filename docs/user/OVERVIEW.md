---
title: "Overview: AI agent access to self-hosted Joplin"
description: "What joplin-md-sync does: Claude and MCP access to self-hosted Joplin notes, a headless Linux service and a reviewable Markdown workflow."
---

# Overview

For testable guarantees, use the [contract catalog](../contracts/README.md).
This page explains the product and its intended use.

`joplin-md-sync` is an independent, open-source bridge between Joplin and the
tools that coding agents and AI assistants already understand.

It provides two complementary ways to work with the same notes:

1. **Typed MCP tools.** Give any compatible assistant structured access to
   notes, notebooks, tags, search, and resources over local stdio or Streamable
   HTTP. Hosted Claude connects through GitHub OAuth that is built into the
   server, so no separate OAuth proxy or gateway is needed.
2. **A reviewable Markdown workspace.** Pull Joplin notes to ordinary files,
   edit them with an agent or local tools, inspect a three-way diff, dry-run the
   exact push plan, and only then update Joplin.

Every MCP transport shares one operation registry. Streamable HTTP MCP and
Claude OAuth also share one listener while keeping their credentials separate.
The file workflow uses a distinct consistency model that prioritizes review and
Git history.

## Why this exists

Joplin is a strong private knowledge base: it is open source, Markdown-native,
supports end-to-end encryption, and lets you choose where data is synchronized.
But an autonomous agent needs more than a text export. It needs stable
identities, machine-readable outcomes, conflict detection, and a write path
that fails clearly.

`joplin-md-sync` adds those contracts without replacing Joplin:

- deterministic JSON responses and stable exit codes;
- true base/local/remote comparison for workspace edits;
- conflict bundles instead of automatic text merging;
- guarded writes that re-read state before applying and verify afterward;
- operation journals and explicit recovery after interruption;
- trash or quarantine instead of default permanent deletion;
- typed MCP schemas backed by one shared tool registry; and
- separate credentials for Joplin, MCP, and OAuth.

## What stays under your control

The tool does not introduce a proprietary note store. Your choices remain
yours:

| Layer | You choose |
| --- | --- |
| Note application | Joplin Desktop or a dedicated Joplin Terminal profile |
| Joplin synchronization | Filesystem, Nextcloud, WebDAV, S3, Joplin Server, Joplin Cloud, or another supported Joplin target |
| Agent access | Claude with built-in GitHub OAuth, local stdio MCP, Streamable HTTP MCP, or Markdown files |
| Change review | CLI diff/dry-run, Git review, client-side MCP approval, or a combination |
| Network boundary | Loopback only, private network, VPN, or a controlled HTTPS publishing layer |

The bridge uses the documented Joplin Data API. It never edits the Joplin
database, profile directory, or sync target directly.

## Common use cases

### A private Joplin assistant

Hosted Claude can search the current Joplin index, read selected notes, create
and update exact objects, and organize notebooks and tags. It signs in through
the built-in GitHub OAuth. The same service gives other MCP clients the same
typed tools, including attachment operations.

### A headless knowledge service

On Linux, one installer runs a dedicated Joplin Terminal profile, recurrent
sync, and the combined MCP and OAuth adapter as coordinated systemd
user services. It asks for the values it needs, including the GitHub OAuth
settings for Claude, hides secrets, generates keys and tokens, and verifies the
result. Joplin syncs to the target you select; its Data API remains private on
loopback, and Joplin Desktop does not need to stay running.

### Project-aware note maintenance

A coding agent can pull a deployment notebook, compare it to the current
repository, update stale commands, show the precise diff, and synchronize the
approved result back to Joplin.

### Git-reviewed knowledge

Selected notebooks can live as Markdown in a Git repository. Joplin identity
is kept in a one-line metadata header, while state databases, credentials,
backups, downloaded resources, and conflict bundles stay outside Git.

### Markdown migration

An intentional `local-first` workspace can turn existing Markdown into new
Joplin notes. The first real push is blocked until a dry-run has recorded the
proposed creations.

## Boundaries

`joplin-md-sync` is not:

- a replacement for Joplin's own device synchronization;
- a hosted notes platform;
- an automatic merge engine for divergent prose;
- a filesystem watcher;
- a direct Joplin database editor; or
- a reason to publish the Joplin Data API to a network.

Start by comparing the integration modes in
[Choose an agent interface](AGENT_INTERFACES.md). Continue with the
[Claude connector](CLAUDE_CONNECTOR.md), the [MCP API](MCP_API.md), the
[headless service](JOPLIN_HEADLESS_SERVICE.md#quick-runbook), or the
[Markdown quick start](GETTING_STARTED.md) for the interface you need.
