<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Development

The [dependency](../contracts/DEPENDENCIES.md) and
[CI and release](../contracts/CI_RELEASES.md) contracts define the testable
repository policy. This page is the maintainer workflow for satisfying it.

## Environments

Use the Make targets rather than installing an ad hoc tool set:

| Target | Purpose |
| --- | --- |
| `make venv` | Editable runtime CLI with no third-party runtime packages |
| `make venv-dev` | Hash-verified Linux quality environment |
| `make venv-test` | Cross-platform test environment |
| `make venv-package` | Packaging environment |
| `make venv-docs` | Documentation environment |
| `make venv-lock` | Isolated resolver bootstrap |

## Daily workflow

1. Read the affected contract assertions and their evidence tests.
2. Add or adjust focused tests with the implementation.
3. Run the smallest relevant test target while iterating.
4. Run `make check` for the portable source gate.
5. Run `make ci` before a pull request when the Linux container/runtime
   prerequisites are available.

Useful targets:

```bash
make format-check
make lint
make typecheck
make bandit
make test
make test-service-installer
make docs-build
make docs-audit
make docs-screenshots
make package
make smoke
```

`make ci` adds coverage, lock drift, strict docs, dependency submission,
workflow lint, and vulnerability audit. Live Joplin acceptance is deliberately
opt-in through `make test-live` because it needs a running Joplin instance and
the ignored repository-root `token` file.

`make test-live-stdio-standalone` builds the current platform's native
executable and runs the read-only stdio acceptance through that artifact. The
test starts no Joplin process; Joplin Desktop and Web Clipper must already be
running locally.

## Documentation changes

Put normative testable behavior in `docs/contracts/`, user tasks and reference
material in `docs/user/`, implementation rationale and maintainer procedures in
`docs/maintenance/`, and build-only inputs in `docs/site/`. Keep the product
homepage at `docs/index.md` because it is the MkDocs site index.

Every new contract assertion needs a permanent ID and at least one exact pytest
or unittest evidence node. `tests/unit/test_documentation_contracts.py`
validates the catalog and source navigation. `make docs-audit` performs a
strict build and then checks the generated HTML, routes, links, anchors,
canonical URLs, sitemap, and assets without using the public network.
`make docs-screenshots` additionally requires Chromium and writes representative
desktop and mobile renders to `.artifacts/docs-screenshots/` for visual review.
