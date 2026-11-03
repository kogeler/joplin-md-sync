<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Contract Catalog

This directory is the only normative specification for current, testable
`joplin-md-sync` behavior. Runtime modules, user guides, and maintainer notes
link here instead of restating requirements. User procedures start in the
[Overview](../user/OVERVIEW.md), design rationale and change procedures start
in [Architecture](../maintenance/ARCHITECTURE.md), and unpublished build inputs
live under `docs/site/`.

The key words `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are
interpreted as described by [BCP 14](https://www.rfc-editor.org/info/bcp14)
when they appear in uppercase. Every normative assertion has a permanent ID
and links to automated checks or declarative integration configuration. This
is a repository-scale requirements verification matrix following NASA guidance on
[requirements traceability and verification](https://www.nasa.gov/reference/system-engineering-handbook-appendix/).

## Contract Format

Every assertion uses this shape:

```text
### `ABC-NNN` - Short title

**Contract:** The implementation MUST ...

**Evidence:**

- [linked test name] - `tests/path.py::TestClass::test_behavior`
```

For native package-manager behavior, evidence can instead link a Make target
as `- [\`lock-check\`](../../Makefile) - \`make lock-check\``. The catalog
validator checks that the target exists; its execution in CI is the validation,
not a unit test of the package manager. Declarative native integrations can
link their TOML/YAML source followed by `- \`configuration\``. Hosted-service
behavior is verified on GitHub, not simulated locally.

IDs are permanent. Changing or removing an assertion requires reviewing its
evidence and every implementation, user, and maintenance reference. External
specifications may explain a requirement but never replace repository
evidence. A successful supported Make test run is the execution record.

## Index

| Contract | Prefix | Scope |
| --- | --- | --- |
| [CLI and output](CLI.md) | `CLI` | Commands, JSON envelope, exit codes, connection resolution, and read-only inspection |
| [Workspace](WORKSPACE.md) | `WSP` | Managed Markdown, notebook identity, paths, and local state layout |
| [Synchronization](SYNCHRONIZATION.md) | `SYN` | Three-way planning, deletes, conflicts, concurrency, journals, and recovery |
| [Agent interfaces](AGENT_INTERFACES.md) | `AIF` | Shared tool registry, MCP, GPT Actions, OpenAPI, limits, and failure behavior |
| [Service](SERVICE.md) | `SVC` | Headless installation, credentials, systemd lifecycle, upgrades, and removal |
| [Security](SECURITY.md) | `SEC` | Token, network, filesystem, content, and destructive-operation boundaries |
| [Dependencies](DEPENDENCIES.md) | `DEP` | Dependency groups, universal lock, isolated environments, audit, and native updates |
| [CI and releases](CI_RELEASES.md) | `CIR` | Workflow topology, permissions, Pages, versions, and publication |
