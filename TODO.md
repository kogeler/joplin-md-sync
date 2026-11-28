# TODO

## Extend OAuth MCP to ChatGPT

`/oauth/mcp` currently permits only the hosted Claude callback. Extend it so
ChatGPT can connect as an MCP client. Version 2.0.0 removed the ChatGPT Actions
interface, so ChatGPT has no supported access until this lands:

- Confirm the redirect URIs and client registration method (DCR or CIMD) that
  ChatGPT uses.
- Add them to an exact-match redirect allowlist without opening loopback or
  wildcard redirects.
- Cover the new client in the OAuth E2E suite and validate it on a live
  deployment.
- Update the connector guide and the documents that state Claude-only support,
  and restore ChatGPT in the site name, README, package and MCP Registry
  descriptions once it works.

## Add semantic search

Search is keyword-only today. Joplin Desktop 3.7 added semantic search
(Beta), but only in the desktop app: it is not available in Joplin Terminal,
which the headless service runs, and the Data API does not expose it. Hosted
Claude therefore cannot search by meaning. Build the same capability into the
adapter so it also works headless.

Joplin's implementation is the reference design
([spec](https://joplinapp.org/help/dev/spec/ai_embeddings)):

- Model `multilingual-e5-small` run locally through ONNX, about 140 MB,
  downloaded on first use.
- Notes split into fixed-size windows that fit the model's 512-token context,
  with 10% overlap and the title added to the first chunk.
- Vectors and metadata in SQLite, searched by cosine similarity, excluding
  trashed and conflict notes.
- A background indexer: one full scan, then a change feed with a durable
  cursor; a model change rebuilds the index.

Plan for this project:

- First confirm on a current Joplin Terminal that neither the Data API nor the
  CLI offers semantic search, so the adapter needs its own index.
- Index through the Data API: a full pass, then incremental updates from
  `/events` with a stored cursor. Run it in the background with bounded memory
  and CPU, and survive Joplin being offline.
- Keep everything local. Use the same multilingual model so Russian and other
  non-English notes work; never send note text to an external embedding API by
  default.
- Store the index in the service state directory with owner-only permissions.
  Embeddings are derived from decrypted note text, so add the storage and
  removal rules to the security contract and remove the index on purge.
- Keep the default installation small: ship the ONNX runtime as an optional
  extra and download the model on first enable with a pinned hash, for wheels
  and native executables alike. Add installer options and prompts.
- Expose a read-only `joplin_semantic_search` tool through the shared registry
  for MCP and OAuth MCP, returning notes with a score and the matching
  excerpt, and state in its description when to prefer it over keyword search.
- Cover indexing, incremental updates, deletion, model change and search
  ranking with tests against the fake and the live Joplin, and document the
  feature in the MCP reference, the comparison with Joplin's built-in MCP, and
  the changelog.
