# Architecture

This page explains the implementation layout and the reasons behind its
boundaries. Normative behavior is owned by the
[contract catalog](../contracts/README.md), especially the
[synchronization](../contracts/SYNCHRONIZATION.md),
[agent-interface](../contracts/AGENT_INTERFACES.md),
[workspace](../contracts/WORKSPACE.md), and
[security](../contracts/SECURITY.md) contracts.

## Module map

```
cli.py          argparse tree, JSON envelope, exit-code mapping, logging
config.py       connection resolution: CLI > env > workspace > discovery
api.py          Joplin Data API client (urllib): pagination, GET retries,
                ambiguous-write surfacing, token redaction, port discovery
mcp_service.py  validated note/notebook/tag/resource operations, relationship
                traversal, base64 limits, bounded Joplin availability waits
mcp_server.py   combined HTTP listener and shared MCP JSON-RPC dispatcher
http_app.py     single-listener legacy/OAuth ASGI routing and framing checks
legacy_asgi.py  ASGI I/O adapter over the same legacy HTTP processing
github_oauth.py GitHub token exchange, numeric owner policy and grant serialization
oauth_server.py embedded FastMCP/ASGI runtime, route validation, lifecycle
oauth_config.py validated CLI options configuration and setup account lookup
oauth_store.py  encrypted, bounded OAuth grants with exclusive process lock
oauth_tools.py  FastMCP provider backed by the shared registry/dispatcher
oauth_http.py   socket admission and read deadlines before request headers
oauth_boundary.py public input limits, safe errors and SDK log suppression
oauth_execution.py bounded shared worker execution without mutation replay
mcp_stdio.py    local newline-delimited MCP transport over stdin/stdout
tool_registry.py shared immutable definitions, Actions exposure, effect mapping
tool_schema.py  dependency-free JSON Schema subset validation
tool_executor.py shared handler invocation and failure classification
gpt_actions.py  authenticated Actions routing, limits, envelopes, audit metadata
gpt_openapi.py  deterministic OpenAPI 3.1 generation and registry hash
canonical.py    body/tag canonicalization + SHA-256 component hashing
metadata.py     managed Markdown header parse/emit (single-line JSON comment)
paths.py        cross-platform filename sanitization, traversal guards
workspace.py    layout, recursive scan, atomic writes, backups, quarantine
state.py        SQLite: base snapshots, tombstones, conflicts, run records,
                integrity check, migration framework
locking.py      exclusive cross-platform lock (fcntl / msvcrt)
journal.py      crash-safe run journal + recovery
planner.py      PURE three-way classification and plan building
sync.py         remote snapshot + executor (guard/apply/verify/commit)
diff.py         summary / name-status / unified / three-way rendering
conflicts.py    bundles, staleness-checked resolution
resources.py    resource download
update_check.py GitHub releases freshness check
errors.py       exception hierarchy = exit codes + result codes
models.py       shared dataclasses and status constants
```

The intended separation is transport (`api`), state (`state` and `workspace`),
decision (`planner`, which is pure), execution (`sync`), and rendering (`cli`
and `diff`). The planner stays easy to exercise because it takes plain data
structures and performs no I/O.

The direct Joplin adapters are deliberately separate from the workspace sync
engine:

```
HTTP MCP client ---> MCP HTTP adapter ----+
Local MCP client --> MCP stdio adapter ---+-> tool registry/executor -> mcp_service -> api -> Joplin
Custom GPT Action -> Actions HTTP adapter +
Hosted Claude -----> OAuth MCP adapter ---+
```

It directly manages Joplin notes, notebooks, tags, and resources and does not
mutate workspace state. The service layer is transport-independent, so future
transports can reuse its validation, metadata shaping, relationship handling,
multipart uploads, and outage behavior.

The agent transports share the same in-process executor. The Actions adapter
never calls `/mcp`, and HTTP controllers never bypass the registry to call the
Joplin client directly. Binary resource read/create/update stays available to
MCP but is explicitly disabled in Actions exposure metadata because its 10 MiB
contract cannot fit the Actions text/payload limit.

GitHub OAuth is installed as part of the standard runtime and activated by
`mcp serve --github-oauth`. The enabled service uses one Uvicorn
worker and one listener: legacy `/mcp`, Actions, OAuth `/claude/mcp`, discovery,
consent and callbacks. The disabled service retains the existing HTTP runtime;
stdio never opens an HTTP listener. Both HTTP implementations use the same
legacy request logic, registry, dispatcher, executor and Joplin service.

OAuth tokens reference encrypted persistent grants. Every resource request
verifies the current GitHub numeric ID through `/user`; positive results are
cached only within that request. One process owns the state directory and
serializes authorization-code exchanges, explicit refresh and transparent
refresh. Removing grants or changing the allowed ID therefore closes access
after restart even for a still-valid signed token. Operator setup and recovery
are described in the [connector guide](../user/CLAUDE_CONNECTOR.md).

### Embedded OAuth design

| Choice | Reason |
| --- | --- |
| `OAuthProxy`, a GitHub identity verifier and a narrow upstream token client | Enforce the verified numeric owner, normalize GitHub refresh TTL fields, and distinguish temporary failures from revocation while retaining SDK endpoints, PKCE, consent and JWT handling. |
| One exchange lock in one HTTP worker | Serialize registration, authorization-code exchange, explicit refresh and transparent refresh without private SDK storage patches or distributed coordination. Lock waits count toward the request deadline. Multiple HTTP workers are unsupported. |
| Request-local positive verification cache | Avoid duplicate GitHub identity lookups within one request while rechecking grants on the next request. |
| Encrypted FileTree storage | Keep grants in a dedicated private directory with a schema, quotas and an exclusive process lock, independently of workspace state and without another database service. |
| Stateless Streamable HTTP with JSON responses | Joplin tools do not need server-push or transport session IDs. Persistent OAuth grants survive restart independently of the transport. |
| Native executable or prepared Python environment | Native builds bundle Python and runtime dependencies. The zipapp contains application code and uses the environment's runtime because binary dependencies cannot be imported directly from the zip. |

### OAuth runtime limits

The following defaults describe the embedded runtime. They complement the
[agent-interface](../contracts/AGENT_INTERFACES.md) and
[security](../contracts/SECURITY.md) contracts. Implementations live in
[socket admission](../../src/joplin_md_sync/oauth_http.py),
[request boundaries](../../src/joplin_md_sync/oauth_boundary.py),
[worker execution](../../src/joplin_md_sync/oauth_execution.py),
[GitHub policy](../../src/joplin_md_sync/github_oauth.py), and
[persistent storage](../../src/joplin_md_sync/oauth_store.py).

| Resource | Default behavior |
| --- | --- |
| Accepted sockets | 16, with admission before HTTP headers arrive |
| Incoming request read deadline | 15 seconds, including empty sockets, partial bodies and pipelined requests |
| Shared synchronous execution | 8 daemon workers; up to 16 admitted running or waiting requests across the interfaces |
| Public OAuth flow rate | A shared token bucket of 60 requests per minute |
| Dynamic client registration | Up to 512 live registered client IDs; expired client records release their identifiers, and working grants are never evicted |
| Request bodies | 16 MiB for `/claude/mcp`, 8 KiB for `/register`, 64 KiB for other OAuth routes |
| Query string | Up to 8 KiB; ambiguous paths/framing, duplicate authorization/form parameters and deeply nested JSON are rejected |
| Flow deadline | 9 seconds normally, 25 seconds for refresh; prebuffered registration/form input has a 2-second read limit within that budget |
| GitHub HTTP | 4-second client timeout; no ambient proxy or automatic redirects |
| Access token lifetime | 3600 seconds by default; configurable from 600 to 3600 seconds |
| Stored state | Up to 4096 files and 256 MiB; records up to 64 KiB, TTL capped at 15897600 seconds |
| Shutdown and cancellation | Bounded draining; an active mutation retains its execution slot until completion, without replay or late private exception logging |

GitHub token responses include an upstream refresh token and positive integer
lifetimes. The adapter maps `refresh_token_expires_in` to the SDK's
`refresh_expires_in` field. API scope `user` includes `read:user`;
`offline_access` is not used as an API permission check. Timeouts, 5xx, 429 and
403 responses carrying rate-limit indicators produce a temporary `503` with a
safe Retry-After. Invalid grants retain their distinct OAuth error.

Host and Origin checks use the configured public authority. Requests without
Origin are supported; forwarded headers do not change the transport peer.
The reverse proxy blocks public health routes and keeps callback code/state out
of raw request logs, as shown in the connector guide.

## Data flow of a mutating command

```
lock workspace (exclusive)
check for incomplete journals            -> exit 6 if any
scan local files                         (workspace.py)
snapshot remote                          (sync.build_remote_snapshot)
classify base/local/remote               (planner.classify — pure)
build ordered operation plan             (planner.build_plan — pure)
[--dry-run stops here]
persist plan to journal                  (journal.begin)
for each op:  guard -> apply -> verify -> commit base -> journal.mark
finalize: cursor, journal complete, prune backups
```

## Key design decisions

1. **One standard service runtime.** FastMCP, ASGI and GitHub OAuth ship with
   the standard package and native executables. CLI configuration enables
   interfaces on the existing listener. Runtime requirements and development
   tools share the root uv lock; platform and distribution tests validate them.
2. **Base snapshots are full copies**, not hashes only. A true three-way
   comparison and offline `status`/`diff --offline` need base content, and
   it lets `build_remote_snapshot` skip body fetches for notes whose
   `updated_time` is unchanged.
3. **Tags are reconciled via the tag map** (`GET /tags` +
   `GET /tags/:id/notes`), because tag attachment does not bump a note's
   `updated_time`. Cost scales with number of tags, not notes.
4. **Optimistic concurrency, no fake locks.** The Joplin API has no
   conditional writes; instead every op re-reads its inputs immediately
   before applying and re-reads the result after, aborting that single op
   (exit 5) on drift. Joplin revisions are never used as versions/ETags.
5. **The journal marks an op applied only after the base commit**, so
   recovery can decide "applied or not" purely from current state, without
   re-running network operations.
6. **Ambiguous writes** (timeout after a PUT) are settled by re-reading:
   result == intended → applied; result == pre-state → reported as failed
   (safe rerun); anything else → concurrent modification.
7. **`/events` is not used in v1** (full reconciliation is cheap at the
   target scale and events cover only notes anyway); the cursor is stored
   for a future optimization.
8. **Filenames are cosmetic.** Identity lives in the header id + state DB;
   pull normalizes names; a rename alone is never a content change.

## State database

The current schema groups persistent information by responsibility:

```
meta          key/value: state_schema_version, event_cursor, ...
notes         id, rel_path, title, body, tags(json), parent_id,
              updated_time, component hashes, combined hash
folders       id, rel_path, title, parent_id
tombstones    note_id, side (local/remote/both), rel_path, title, time
conflicts     id, note_id, rel_path, category, created_time, status
journal_runs  run_id, command, started_time, status, journal_path
```

Schema compatibility and migration behavior are specified by
[`SYN-009`](../contracts/SYNCHRONIZATION.md#syn-009-state-corruption-and-schema-drift-fail-closed).
Implementation changes start in `state.MIGRATIONS` and must update the linked
evidence before this explanatory inventory is changed.
