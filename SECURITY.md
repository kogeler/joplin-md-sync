# Security Policy

## Reporting

Report suspected vulnerabilities via GitHub private vulnerability reporting
(Security tab) on this repository. Do not open public issues for security
problems.

## Security model

- The only network endpoints ever contacted are the configured local Joplin
  Data API and (optionally, `update-check` only) the public GitHub Releases
  API. Note content is never sent anywhere else.
- Non-loopback Joplin addresses are refused unless `--allow-remote-api` is
  given explicitly.
- The token comes from `JOPLIN_TOKEN` or `--token-file`; it is never accepted
  as a raw CLI argument, never logged, redacted from all errors and debug
  output, and never written into the workspace or any Git-tracked file.
- Markdown content and filenames are treated as untrusted input: no shell is
  ever invoked on note content, symlinks are not followed, and any workspace
  path resolving outside the root is rejected.
- Deletions are conservative: Joplin trash only (never `permanent=1`), local
  quarantine under `.joplin-sync/quarantine/`, both only with
  `--propagate-deletes`.

See docs/SECURITY.md for the detailed threat model.
