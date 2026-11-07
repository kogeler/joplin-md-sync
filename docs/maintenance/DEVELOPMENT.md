<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Development

The [dependency](../contracts/DEPENDENCIES.md) and
[CI and release](../contracts/CI_RELEASES.md) contracts define the testable
repository policy. This page is the maintainer workflow for satisfying it.

## Environments

Use the Make targets rather than installing an ad hoc tool set:

| Target | Purpose |
| --- | --- |
| `make venv` | Editable CLI with all standard runtime dependencies |
| `make venv-dev` | Locked quality group and standard runtime |
| `make venv-test` | Cross-platform tests and standard runtime, including required OAuth E2E |
| `make venv-package` | Build tools and standard runtime for native packaging |
| `make venv-docs` | Documentation environment |

Every environment lives under `.venvs/<key>/`, for example
`.venvs/<key>/venv-test`. `scripts/venv_root.py` derives the key from the OS
machine identity (`/etc/machine-id` on Linux, the registry `MachineGuid` on
Windows) and the local user, and prints only a hash of them. One checkout on a
shared or network drive can therefore be used from several hosts without one
host reusing or deleting another host's interpreter links and binaries. Without
a valid machine identity Make stops instead of falling back to a shared
directory.

`make clean` removes only the current host's environments. Environments of
other hosts and legacy root-level `venv*` directories from older checkouts are
left untouched; delete an obsolete one manually.

## Daily workflow

1. Read the affected contract assertions and their evidence tests.
2. Add or adjust focused tests with the implementation.
3. Run the smallest relevant test target while iterating.
4. Run `make check` for the portable source gate.
5. Run `make ci` before a pull request when the Linux container/runtime
   prerequisites are available.

Install uv before running Make. CI uses `.tool-versions` through the official
setup-uv action. Every group is installed with `uv sync --locked` into its
own host-scoped environment. No target rewrites `uv.lock` unless explicitly
asked through `make lock` or `make refresh-dependencies`.

Useful targets:

```bash
make format-check
make lint
make typecheck
make bandit
make test
make test-service-installer
make lock-check
make docs-build
make docs-audit
make docs-screenshots
make package
make smoke
```

`make ci` adds coverage, native lock validation, strict docs, workflow lint, and
an audit of all locked dependency groups. Podman or Docker is needed for the
immutable workflow linter. Reusable CI follows the quality job with a
separate Linux AMD64 `make test-live` gate. The live target downloads the
checksum-pinned official Joplin Desktop 3.6.15 Debian binary, extracts it
without installing system packages, and runs it with Xvfb, `HOME`, XDG paths,
and its profile isolated under `/tmp`. Python stops the process groups and
removes the complete temporary tree after the session. Local runs require
network access, `dpkg-deb`, and Xvfb; they do not use a running Joplin instance
or the repository-root `token` file.

`make test-live-stdio-standalone` builds the current platform's native
executable and runs the read-only stdio acceptance through that artifact
against the same ephemeral Joplin Desktop runtime. Both live targets are Linux
AMD64 only.

## OAuth acceptance and evidence

The standard `make test` and `make test-full` targets include mandatory OAuth
E2E scenarios. Focused runs use `make test-oauth` or `make test-oauth-e2e`.
The `--require-oauth-e2e` gate compares collected E2E case IDs exactly with
the inventory, rejecting missing, unlisted or duplicate cases, and fails
skipped, xfailed, xpassed or failed required cases.

| Check | Coverage |
| --- | --- |
| `make test-oauth-e2e` | A separate production CLI process, real HTTP/TLS, DCR, both PKCE flows, consent, owner policy, every registered tool, refresh, restart, replay and failure behavior |
| `make test-full` | The full source suite, mandatory OAuth E2E, JUnit results and source coverage |
| `make test-service-installer` | Real installer processes and controlling TTYs, hidden prompts, update/reconfigure/clean, private files, source-ref selection and temporary bundle cleanup |
| `make test-live` | OAuth and other agent interfaces against an isolated real Joplin Desktop |
| `make test-service-live` | Real Terminal installation, signed S3 round trip and the deployed supervisor |
| `make package` and `make smoke` | Source/wheel/sdist/zipapp runtime behavior, including OAuth off/on, discovery, registration and consent outside the checkout, and the GitHub TLS client probe in each runtime |
| `make standalone checksums smoke-standalone` | Native executable packaging, integrity metadata and the same runtime smoke; the TLS probe runs in a probe build with the release PyInstaller options |
| `make test-oauth-systemd` | The built native executable in a real Linux user sandbox: scoped state writes, surrounding read-only paths, restart, disable/reset and managed purge |

OAuth E2E replaces the external GitHub endpoints and controls the clock while
retaining production parsers, middleware, cryptography, persistent storage,
registry, executor and Joplin HTTP client. Unexpected external DNS is rejected.
The child binds a kernel-assigned test port and reports it to the parent;
readiness also checks the instance's unique Bearer credential. Package smoke
and native systemd acceptance exercise the standard launcher and bind path.

The [functional matrix](../../tests/oauth/e2e/requirements.json) maps 24 pytest
requirements to exact case IDs, `file.py::function[parameter-id]`; packaging and
systemd have separate gates. Only genuinely platform-specific cases appear under
`platforms`, with `os.name` values `posix` or `nt`. Elsewhere the gate skips such
a case and the report marks it not applicable, so it never counts there; where
it applies, a skip fails. SIGTERM draining is a POSIX case; Windows has its own
forced-termination, restart and lock-recovery case.

After an intentional test change, refresh the inventory deliberately from a
full collection, then review its diff:

```bash
make oauth-e2e-inventory
```

The refresh keeps existing mappings, drops removed cases and adds a new
parameter variant to every requirement that lists its function. Any other new
case is written under `unmapped`, which the gate rejects: move each such ID into
the `cases` of every requirement it proves, add a `platforms` entry only if it is
platform-specific, and run the refresh again.
The [service contract](../contracts/SERVICE.md) indexes installer evidence.
`make test-oauth-e2e` writes `.artifacts/oauth-e2e-evidence.json` itself. After
`make test-full`, generate the report from the complete JUnit results:

```bash
python scripts/report_oauth_acceptance.py \
  --junit .artifacts/test-results.xml \
  --output .artifacts/oauth-e2e-evidence.json
```

The report records each requirement's outcome, every case's status, missing,
unlisted, duplicate and rejected case IDs, and source hashes; missing, malformed
or incomplete JUnit is an explicit error. Whenever JUnit exists, CI generates
the report and uploads it with the available JUnit and coverage files, even
after failed tests, and retains CodeQL SARIF separately. Functional coverage is distinct from line coverage;
platform-specific skips outside mandatory OAuth E2E remain visible.

A real hosted Claude login is validated through the
[connector guide](../user/CLAUDE_CONNECTOR.md#connect-claude-and-verify), using
operator credentials and a browser. Mocked acceptance does not establish that
an operator's deployed hostname, proxy and connector are configured correctly.

## Documentation changes

Put normative testable behavior in `docs/contracts/`, user tasks and reference
material in `docs/user/`, implementation rationale and maintainer procedures in
`docs/maintenance/`, and build-only inputs in `docs/site/`. Keep the product
homepage at `docs/index.md` because it is the MkDocs site index.
Write all project material in English. Keep working plans, research notes and
task trackers in Git-ignored `tmp/`, outside published documentation and site
navigation. Extract durable guidance into these maintained guides. Follow the
root agent runbook's explicit authorization rules for commits and pushes.

Every new contract assertion needs a permanent ID and at least one exact pytest
or unittest evidence node. `tests/unit/test_documentation_contracts.py`
validates the catalog and source navigation. `make docs-audit` performs a
strict build and then checks the generated HTML, routes, links, anchors,
canonical URLs, sitemap, and assets without using the public network.
`make docs-screenshots` additionally requires Chromium and writes representative
desktop and mobile renders to `.artifacts/docs-screenshots/` for visual review.
