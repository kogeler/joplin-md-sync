# Security Notes (detailed)

Policy and reporting: see the repository-root [SECURITY.md](../SECURITY.md).

## Threat model

Assets: the Joplin token; note content; the local filesystem outside the
workspace. Adversarial inputs: note titles/bodies/tags coming from Joplin,
files dropped into the workspace, and the local network.

## Controls

**Token.**
- Accepted only via `JOPLIN_TOKEN` or `--token-file` (a raw `--token`
  argument would leak into the process list and shell history).
- Every log record and error message passes a redaction filter; the API
  client redacts request URLs and HTTP error bodies before raising.
- Never written to `workspace.json`, the state DB, journals, bundles, or
  any Git-tracked file; CI checks release artifacts for token-like strings.

**Network.**
- Only two destinations exist: the configured Joplin endpoint and (for
  `update-check` only) `api.github.com`. There is no telemetry.
- Non-loopback Joplin endpoints require `--allow-remote-api`.
- Discovery accepts only servers answering `GET /ping` with the exact
  Clipper banner and errors out when zero or multiple match.
- MCP Streamable HTTP binds to `127.0.0.1` by default, validates browser
  `Origin`, caps request bodies, and rejects remote binds unless both an
  explicit override and MCP bearer authorization are configured.

**MCP authorization.**
- Disabled by default for the loopback-only listener.
- `--auth-token-file` enables constant-time comparison of a pre-shared bearer
  secret. The file is re-read per request for rotation, and its token is never
  forwarded to Joplin or accepted in a URL/CLI value.
- The MCP bearer and Joplin API token are separate credentials. The built-in
  mode is not an OAuth authorization-server flow; network deployments need TLS
  termination and access controls in a trusted reverse proxy.

**Filesystem.**
- Symlinks are never followed (reported as `INVALID_LOCAL_FILE`).
- Every path derived from remote titles is sanitized (Windows-invalid
  characters, reserved device names, trailing dots/spaces, `..`
  components) and verified to resolve inside the workspace root.
- Writes are atomic (`tempfile` + `os.replace`) and verified by re-reading.
- Destructive operations keep recoverable copies (backups / quarantine).

**Content.**
- Note content is treated as data only: no shell is ever invoked on it
  (`subprocess` is not used with note-derived input at all), and Markdown
  is never rendered or executed.

## Residual risks

- Anyone with local access to the Joplin Clipper port and token has full
  note access — that is Joplin's own trust boundary, not extended by this
  tool.
- `--allow-remote-api` sends the token and note content over whatever
  transport the given URL uses; use HTTPS and trusted networks only.
- `--allow-remote-mcp` exposes note tools to a network. Even with bearer auth,
  plain HTTP reveals credentials and content to the network; use a TLS reverse
  proxy and firewall rules.
- MCP requests are capped at 16 MiB and decoded resource bodies at 10 MiB per
  item. Binary input is accepted only as base64 request data; MCP tools never
  accept a server-side filesystem path.
- Note and notebook deletion uses Joplin trash. Tag and resource deletion is
  permanent because Joplin has no trash API for those types; both tools are
  advertised as destructive and should remain confirmation-gated by clients.
