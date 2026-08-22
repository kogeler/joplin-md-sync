# Self-hosted deployment

Deployment guarantees and trust boundaries are owned by the
[service](../contracts/SERVICE.md), [security](../contracts/SECURITY.md), and
[agent-interface](../contracts/AGENT_INTERFACES.md) contracts.

Self-hosting is not one switch. It is control over four separate layers:

1. where the Joplin profile runs;
2. where Joplin synchronizes encrypted or unencrypted data;
3. where the agent bridge listens; and
4. which bridge routes are reachable by each client.

`joplin-md-sync` supports a headless Linux topology and a local desktop
topology. Neither requires publishing Joplin's own Data API.

## Topology A: headless Linux host

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
temporarily unavailable and retries availability on later calls. Joplin
Desktop does not need to run on this host or remain online elsewhere.

The installer supports filesystem, OneDrive, Nextcloud, WebDAV, Dropbox, S3,
Joplin Server, Joplin Cloud, and Joplin Server SAML targets. Browser-based and
password-based targets have different setup paths; use the canonical
[service installation guide](SERVICE.md#headless-linux-installation) instead
of constructing profile commands manually.

## Topology B: local desktop

```text
agent or MCP client
        |
        | stdio, Markdown files, or http://127.0.0.1:8765/mcp
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

- an editor or desktop assistant supports local MCP;
- a coding agent runs on the same workstation;
- you want Git-reviewed Markdown changes; or
- you are evaluating the service before operating a server.

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

- run the headless profile against your own filesystem, WebDAV, Nextcloud, S3,
  or Joplin Server;
- keep Joplin Desktop as the only active profile and use its normal sync;
- use Joplin's end-to-end encryption when supported by the chosen topology;
- publish only the narrow agent interface you need; and
- remove the headless service without deleting remote sync data.

This preserves the important ownership boundary: Joplin remains the note
system, the selected sync target remains the data transport, and
`joplin-md-sync` remains a replaceable adapter.

## Recommended rollout

1. Choose the Joplin sync target and whether the client needs Actions, MCP, or
   both.
2. Review the headless installer dry-run and its planned filesystem and service
   changes.
3. Install the coordinated services and verify recurrent Joplin sync.
4. Keep the Joplin Data API on loopback and retain the three separate generated
   credentials.
5. Publish only the required adapter routes behind TLS and explicit network
   policy.
6. Configure the private GPT or MCP client with its dedicated credential.
7. Run the live acceptance checks before relying on the service.
8. If you also create a Markdown workspace, pull after every direct MCP or
   Actions write and never interleave the two write models.

For commands, installer options, upgrades, rollback, removal, and
troubleshooting, continue to [Service operations](SERVICE.md).
