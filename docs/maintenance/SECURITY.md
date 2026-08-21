<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Security Rationale

Testable security requirements live only in the
[security contract](../contracts/SECURITY.md). Transport-specific boundaries
are in the [agent-interface contract](../contracts/AGENT_INTERFACES.md), and
headless installation guarantees are in the
[service contract](../contracts/SERVICE.md).

## Threat model

The project assumes note bodies, titles, tags, filenames, HTTP requests, and
remote API responses are untrusted. It also assumes a local user may point the
client at the wrong endpoint or expose a listener accidentally. The design
therefore concentrates authority in explicit credential files and explicit
remote-access flags, validates data before side effects, and keeps note content
out of commands and audit records.

The Joplin Data API lacks conditional writes and permanent-delete recovery for
some object types. Concurrency checks and operation metadata make these limits
visible instead of implying stronger backend guarantees. The filesystem side
uses traversal checks, symlink rejection, atomic replacement, backups, and
quarantine to keep local failures inspectable.

## Trust boundaries

- The local CLI trusts its process environment and explicitly selected token
  file, but never the note content it reads.
- The MCP and Actions listener authenticates callers independently from the
  private Joplin API credential.
- The reverse proxy terminates public TLS; it does not make the Joplin Data API
  public.
- GitHub workflows receive only the permissions required by their event and
  never provide release credentials to pull-request code.
- Generated documentation is public and must contain no credentials or live
  note data.

## Residual risk

A process running as the same operating-system user can generally read that
user's protected files and Joplin profile. Bearer tokens authorize the holder,
so host compromise or copied credentials remain outside the application's
boundary. Public deployments also depend on correct firewall, TLS proxy, DNS,
and systemd configuration.

## Change procedure

1. Map the change to existing `SEC-*`, `AIF-*`, and `SVC-*` assertions or add a
   new assertion with evidence.
2. Test the failure path and prove that no secret or note content appears in
   stdout, stderr, logs, generated contracts, or service units.
3. Review registry effect metadata for every new destructive operation.
4. Run `make bandit`, the focused integration tests, and `make check`.

Deployment instructions are in
[Self-hosted service](../user/SELF_HOSTED.md) and
[Service operations](../user/SERVICE.md).
