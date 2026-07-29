# Self-hosted deployment

Self-hosting is not one switch. It is control over four separate layers:

1. where the Joplin profile runs;
2. where Joplin synchronizes encrypted or unencrypted data;
3. where the agent bridge listens; and
4. which bridge routes are reachable by each client.

`joplin-md-sync` supports a local desktop topology and a headless Linux
topology. Neither requires publishing Joplin's own Data API.

## Topology A: local desktop

```text
agent or MCP client
        |
        | Markdown files or http://127.0.0.1:8765/mcp
        v
joplin-md-sync
        |
        | http://127.0.0.1:41184 + Web Clipper token
        v
Joplin Desktop -> your configured Joplin sync target
```

This is the smallest trust boundary. Keep Joplin Desktop running during online
operations. The bridge and Joplin Data API both remain on loopback.

Use it when:

- a coding agent runs on the same workstation;
- you want Git-reviewed Markdown changes;
- an editor or desktop assistant supports local MCP; or
- you are evaluating the service before operating a server.

## Topology B: headless Linux host

```text
MCP client on private network       ChatGPT
             |                         |
             | authenticated TLS       | HTTPS Actions
             +------------+------------+
                          |
                joplin-md-sync.service
                 /mcp   /api/gpt/v1/*
                          |
                          | loopback only
                          v
                 joplin-terminal.service
                          |
                          v
                 your Joplin sync target
```

The included non-root installer creates two coordinated systemd user services:

- `joplin-terminal.service` owns a dedicated Joplin Terminal profile, recurrent
  sync, and loopback Data API on port 41185 by default;
- `joplin-md-sync.service` owns the combined MCP and Actions listener on port
  8765 by default.

There is no separate Actions service. The bridge starts even when Joplin is
temporarily unavailable and retries availability on later calls.

The installer supports filesystem, OneDrive, Nextcloud, WebDAV, Dropbox, S3,
Joplin Server, Joplin Cloud, and Joplin Server SAML targets. Browser-based and
password-based targets have different setup paths; use the canonical
[service installation guide](SERVICE.md#headless-linux-installation) instead
of constructing profile commands manually.

## Network boundaries

Treat each URI independently:

| URI | Recommended exposure |
| --- | --- |
| Joplin Data API (`41184` or `41185`) | Loopback only |
| `/api/gpt/v1/*` | Public only through authenticated HTTPS when Actions are required |
| `/mcp` | Loopback, VPN, or trusted private network by default; remote access requires its own bearer token and TLS |
| `/healthz`, `/readyz` | Operator checks; return no note data or credentials |

Remote MCP binding is refused unless both `--allow-remote-mcp` and a protected
MCP bearer-token file are configured. That check does not provide TLS; put a
trusted reverse proxy or private encrypted network in front of it.

The Actions token, MCP token, and Joplin Data API token must all differ. They
serve different trust boundaries and are never interchangeable.

## What "self-hosted notes" means here

The bridge does not force one storage product. You can:

- keep Joplin Desktop as the only active profile and use its normal sync;
- run the headless profile against your own filesystem, WebDAV, Nextcloud, S3,
  or Joplin Server;
- use Joplin's end-to-end encryption when supported by the chosen topology;
- publish only the narrow agent interface you need; and
- remove the headless service without deleting remote sync data.

This preserves the important ownership boundary: Joplin remains the note
system, the selected sync target remains the data transport, and
`joplin-md-sync` remains a replaceable adapter.

## Recommended rollout

1. Complete the [local quick start](GETTING_STARTED.md) and make one reviewed
   Markdown change.
2. Test loopback MCP with a read-only notebook or note listing.
3. Decide whether the client actually needs remote access.
4. Install the headless services using the documented dry-run.
5. Keep the Joplin Data API on loopback.
6. Add separate MCP and Actions credentials for the interfaces you enable.
7. Put remote routes behind TLS and explicit network policy.
8. Run the live acceptance checks before relying on the service.

For commands, installer options, upgrades, rollback, removal, and
troubleshooting, continue to [Service operations](SERVICE.md).

