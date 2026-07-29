# Overview

`joplin-md-sync` is an independent, open-source bridge between Joplin and the
tools that coding agents and AI assistants already understand.

It provides two complementary ways to work with the same notes:

1. **A reviewable Markdown workspace.** Pull Joplin notes to ordinary files,
   edit them with an agent or local tools, inspect a three-way diff, dry-run the
   exact push plan, and only then update Joplin.
2. **Structured agent APIs.** Run one local service that exposes typed MCP
   tools and authenticated ChatGPT Actions for notes, notebooks, tags, search,
   and resources.

These interfaces deliberately remain separate. The file workflow prioritizes
review and Git history. MCP and Actions prioritize immediate, structured
operations.

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
- typed MCP and Actions schemas backed by one shared tool registry; and
- separate credentials for Joplin, MCP, and ChatGPT Actions.

## What stays under your control

The tool does not introduce a proprietary note store. Your choices remain
yours:

| Layer | You choose |
| --- | --- |
| Note application | Joplin Desktop or a dedicated Joplin Terminal profile |
| Joplin synchronization | Filesystem, Nextcloud, WebDAV, S3, Joplin Server, Joplin Cloud, or another supported Joplin target |
| Agent access | Markdown files, local MCP, remote authenticated MCP, or ChatGPT Actions |
| Change review | CLI diff/dry-run, Git review, client-side MCP approval, or a combination |
| Network boundary | Loopback only, private network, VPN, or a controlled HTTPS publishing layer |

The bridge uses the documented Joplin Data API. It never edits the Joplin
database, profile directory, or sync target directly.

## Common use cases

### Project-aware note maintenance

A coding agent can pull a deployment notebook, compare it to the current
repository, update stale commands, show the precise diff, and synchronize the
approved result back to Joplin.

### A private Joplin assistant

An MCP client can search the current Joplin index, read selected notes, update
tags, create structured notes, and work with attachments. A private Custom GPT
can use the same service through generated Actions.

### Git-reviewed knowledge

Selected notebooks can live as Markdown in a Git repository. Joplin identity
is kept in a one-line metadata header, while state databases, credentials,
backups, downloaded resources, and conflict bundles stay outside Git.

### A headless knowledge service

On Linux, the included installer can run a dedicated Joplin Terminal profile
and the combined MCP/Actions adapter as coordinated systemd user services.
Joplin syncs to the target you select; its Data API remains private on
loopback.

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

Start with [Installation and quick start](GETTING_STARTED.md), or compare the
available integration modes in [Choose an agent interface](AGENT_INTERFACES.md).

