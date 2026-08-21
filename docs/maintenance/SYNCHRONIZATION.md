<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Synchronization Design

The normative synchronization behavior is defined by the
[synchronization contract](../contracts/SYNCHRONIZATION.md). Workspace
identity and file rules belong to the
[workspace contract](../contracts/WORKSPACE.md), while command and output
behavior belongs to the [CLI contract](../contracts/CLI.md).

## Design model

Each managed note has three relevant versions: the stored base snapshot, the
current local file, and the current remote Joplin item. `planner.py` classifies
those values without I/O. `sync.py` obtains snapshots and applies the ordered
plan. This split keeps policy reviewable and lets tests cover the complete
classification matrix without a running Joplin instance.

Full base content is retained because a hash alone cannot render a three-way
diff or support useful offline inspection. The cost is bounded by the intended
personal-notebook scale and avoids depending on Joplin's revision history as a
concurrency primitive.

Tags are collected through the tag-to-note relationship map. Joplin does not
change a note's `updated_time` when only its tag attachments change, so note
pagination alone cannot produce a complete remote snapshot.

Joplin does not expose conditional note writes. The executor therefore reads
inputs immediately before a mutation and verifies the result immediately
after it. A journal records the plan and each committed base update so recovery
can decide what completed without replaying uncertain network operations.

Filenames are presentation. Note IDs in managed headers and folder IDs in
metadata carry identity; pull is free to normalize cosmetic paths after the
planner has matched objects.

## Change procedure

1. Identify the affected `SYN-*`, `WSP-*`, and `CLI-*` assertions.
2. Add or update the evidence test before changing planner or executor policy.
3. Keep classification in `planner.py`; do not add network or filesystem I/O
   to it.
4. Exercise interruptions at each journal boundary for executor changes.
5. Run focused planner, integration, recovery, and contract tests, then
   `make check`.

Useful focused commands:

```bash
venv-test/bin/python -m pytest -q tests/unit/test_planner.py
venv-test/bin/python -m pytest -q tests/integration/test_push_pull.py
venv-test/bin/python -m pytest -q tests/integration/test_races_recovery.py
venv-test/bin/python -m pytest -q tests/unit/test_documentation_contracts.py
```

The user-facing operating procedures are in
[Agent workflows](../user/AGENT_WORKFLOWS.md),
[Workspace format](../user/WORKSPACE_FORMAT.md), and
[Conflict handling](../user/CONFLICTS.md).
