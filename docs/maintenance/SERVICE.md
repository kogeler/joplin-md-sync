<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Service Maintenance

The headless installer's normative behavior belongs to the
[service contract](../contracts/SERVICE.md). Authentication and transport
requirements belong to the [security](../contracts/SECURITY.md) and
[agent-interface](../contracts/AGENT_INTERFACES.md) contracts. User setup,
operation, troubleshooting, and manual acceptance remain in
[Service operations](../user/SERVICE.md).

## Components

`scripts/joplin_terminal_service/install_joplin_terminal.py` owns argument
parsing, dependency and platform validation, installation, service-unit
rendering, upgrades, and removal. `run_joplin_terminal.py` supervises Joplin
Terminal, unlocks existing E2EE state, and verifies Data API health.
`collect_joplin_debug.sh` produces a secret-safe diagnostic report.

The installer creates two user services. `joplin-terminal.service` owns the
Joplin profile and private Data API. `joplin-md-sync.service` depends on it and
exposes the authenticated combined MCP and Actions listener. Keep the backend
credential, MCP bearer, and Actions bearer as separate trust domains.

## Automated tests

Linux CI runs the dependency-free installer suite through:

```bash
make test-service-installer
```

To reproduce it in a clean container:

```bash
podman run --rm \
  -v "$PWD:/workspace:ro" \
  -w /workspace/scripts/joplin_terminal_service \
  python:3.14-slim \
  python3 -m unittest discover -s tests -v
```

The exact behavioral coverage is indexed by the evidence links in the
[service contract](../contracts/SERVICE.md). Protocol changes also require the
regular integration suite and `make test-live`. That target provisions its own
checksum-verified Joplin Desktop 3.6.15 binary and temporary profile on Linux
AMD64; it never uses the operator's running Joplin instance.

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
