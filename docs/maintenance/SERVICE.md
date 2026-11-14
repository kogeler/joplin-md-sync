<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Service Maintenance

The headless installer's normative behavior belongs to the
[service contract](../contracts/SERVICE.md). Authentication and transport
requirements belong to the [security](../contracts/SECURITY.md) and
[agent-interface](../contracts/AGENT_INTERFACES.md) contracts. User setup,
operation, troubleshooting, and sync acceptance remain in
[Joplin Headless Service](../user/JOPLIN_HEADLESS_SERVICE.md).

## Components

`scripts/joplin_terminal_service/install_joplin_terminal.py` is the small
bootstrap entry point. Local runs import the sibling package. Downloaded or
stdin runs create a private directory under `/tmp`, verify
`installer-bundle.json` against the digest pinned in the entry point, verify
every module/asset, and import `joplin_terminal_installer.application` normally.
Remote source selection supports `--installer-ref`/`--ref` or
`JOPLIN_TERMINAL_INSTALLER_REF` (default `main`), including branch names with
slashes, tags and commit SHAs. The entry point and bundle must use the same
ref; hashes prevent mixing revisions. The complete bundle stays available throughout setup and is removed on exit.
Installed services use deployed persistent files, never the temporary bundle.

The package separates `cli`, `prompts`, `commands`, `releases`, `credentials`,
`sync`, `encryption`, `oauth`, `managed`, `service`, `settings`, and
`application`, `lifecycle`, and `operations`. Shared primitives live in `common`; `assets` reads the complete
local/downloaded bundle. The root `joplin_terminal_common.py` re-exports the
shared primitives for source supervisor compatibility; deployment copies the
common implementation beside the supervisor.

After editing bundled Python sources or templates, refresh integrity metadata:

```bash
python3 scripts/joplin_terminal_service/build_bundle_manifest.py --write
```

`make test-service-installer` checks the manifest before running tests. The
entry point has no installer dependencies beyond the standard library. `run_joplin_terminal.py` supervises Joplin
Terminal, unlocks existing E2EE state, and verifies Data API health.
`collect_joplin_debug.sh` produces a secret-safe diagnostic report.

The installer creates two user services. `joplin-terminal.service` owns the
Joplin profile and private Data API. `joplin-md-sync.service` depends on it and
exposes the authenticated combined MCP and Actions listener. Keep the backend
credential, MCP bearer, and Actions bearer as separate trust domains.

## Automated tests

Linux CI runs the installer suite and real Terminal acceptance through:

```bash
make test-service-installer
make test-service-live
```

The bootstrap itself uses only the standard library. The complete installer
test suite also launches the production MCP/OAuth CLI, so it needs the standard
project runtime. `make test-service-installer` provisions that locked runtime;
a bare Python container is insufficient for the complete acceptance suite.

The exact behavioral coverage is indexed by the evidence links in the
[service contract](../contracts/SERVICE.md). Protocol changes also require the
regular integration suite and `make test-live`. That target provisions its own
checksum-verified Joplin Desktop 3.6.15 binary and temporary profile on Linux
AMD64; it never uses the operator's running Joplin instance.

`make test-service-live` builds an isolated Python/Node container and invokes
the real installer to install Joplin Terminal 3.7.1. Runtime acceptance has no
external network: two temporary profiles and a local S3 protocol fixture
verify signed requests (including presigned GET), custom endpoint paths, a
non-AWS region, prompt-based sender setup, optional credential-file receiver
setup, note upload/download, and wrong-key rejection even when Joplin
exits zero. It does not use an external S3 provider account, production notes,
or a systemd user manager. It also starts the real Terminal through the
supervisor, checks Data API readiness and verifies clean termination. CI runs
this target in the live acceptance job; installer unit tests separately cover
lifecycle policy.
The process suite also runs the complete piped Python entry point against
local download/host-program peers, checks hidden prompts, zero-argument
install/update, partial OAuth configuration, reconfiguration cleanup,
prompt-free removal, bundle tampering and temporary-directory cleanup.
The installer suite uses a real PTY to verify that both default S3 key prompts
hide input; the documentation tests keep the primary example interactive.

## Change procedure

1. Update or add the affected `SVC-*`, `SEC-*`, and `AIF-*` assertion and its
   evidence.
2. Keep installer validation ahead of downloads, writes, and service changes.
3. Exercise dry-run, rerun, upgrade, rollback, and purge paths for lifecycle
   changes.
4. Verify rendered units with `systemd-analyze` where available and inspect
   them for secret values.
5. Update the complete option and environment tables in the user guide; tests
   compare them with the parser.
6. Run the installer suite, `make check`, and `make docs-build`.
