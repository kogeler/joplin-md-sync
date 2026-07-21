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
  `Origin`, caps request bodies, limits simultaneous HTTP handlers, times out
  stalled connections, and rejects remote binds unless both an explicit
  override and MCP bearer authorization are configured.

**MCP authorization.**
- Disabled by default for the loopback-only listener.
- `--auth-token-file` enables constant-time comparison of a pre-shared bearer
  secret. MCP and Actions token files must be current-user-owned regular files,
  inaccessible to group/others on POSIX, never symlinks, and contain one
  bounded URL-safe Base64 value encoding at least 32 bytes. Files are re-read
  per request, and tokens are never forwarded to Joplin or accepted in a
  URL/CLI value.
- Malformed, non-ASCII, oversized, or duplicated `Authorization` values fail
  closed. Comparisons use `hmac.compare_digest` on ASCII bytes, so malformed
  text cannot raise from the comparison boundary.
- The MCP bearer and Joplin API token are separate credentials. The built-in
  mode is not an OAuth authorization-server flow; network deployments need TLS
  termination and access controls in a trusted reverse proxy.

**GPT Actions.**
- Disabled by default and enabled only with `--gpt-actions` plus a dedicated
  protected token file. The token is re-read per request and compared in
  constant time; startup rejects equality with Joplin or MCP credentials.
- Every public tool route authenticates before route lookup. Requests have
  strict JSON schemas and size limits; execution has bounded concurrency and
  authenticated rate limiting. Logs contain request IDs, timings, sizes,
  effect, status, and result class, never headers, arguments, results, or note
  content.
- Only `/api/gpt/v1/*` is intended for public HTTPS exposure. The generated
  OpenAPI contract contains no credential, user data, local path, MCP route,
  health route, or Joplin URL.
- Joplin content is untrusted data. Server controls enforce schemas,
  authentication, exposure metadata, and effect classification; model
  instructions are defense-in-depth, not an authorization boundary.
- Arguments and results used by a Custom GPT pass through OpenAI's systems.
  Operators should request and return only the content needed for the current
  task.

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
- A generated 32-byte bearer secret has a 256-bit search space. Online request
  throttling is not a substitute for that entropy; operators must not replace
  generated credentials with human-chosen strings.
- Note and notebook deletion uses Joplin trash. Tag and resource deletion is
  permanent because Joplin has no trash API for those types; both tools are
  advertised as destructive and should remain confirmation-gated by clients.
- The incoming socket timeout bounds stalled network reads, but a client-side
  timeout does not cancel an in-flight tool execution thread. The server does
  not automatically retry writes; ambiguous and partial writes must be
  inspected in Joplin before another attempt.
