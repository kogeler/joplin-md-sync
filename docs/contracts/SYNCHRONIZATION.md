<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Synchronization Contract

## Assertions

### `SYN-001` - Planning is a deterministic three-way comparison

**Contract:** Planning MUST classify complete base, local, and remote note and
notebook state without I/O and produce a stable operation order. Local-only
changes MUST push, remote-only changes MUST pull, equal concurrent changes MUST
rebase, unequal concurrent changes MUST conflict, and malformed or duplicate
local identity MUST be blocked.

**Evidence:**

- [`test_local_only_body_change`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_local_only_body_change`
- [`test_remote_only_change`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_remote_only_change`
- [`test_same_change_both_sides`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_same_change_both_sides`
- [`test_divergent_change_is_conflict`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_divergent_change_is_conflict`
- [`test_duplicate_local_ids_invalid`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_duplicate_local_ids_invalid`
- [`test_plan_is_deterministic`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::PlanDeterminismTest::test_plan_is_deterministic`

### `SYN-002` - Dry-run never mutates either side

**Contract:** Pull, push, and sync dry-runs MUST stop after planning, report the
exact pending operation list, return exit `1` when work exists, and leave local
files, internal state, and Joplin unchanged.

**Evidence:**

- [`test_dry_run_mutates_nothing`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_dry_run_mutates_nothing`
- [`test_local_first_requires_dry_run_before_push`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::LocalFirstModeTest::test_local_first_requires_dry_run_before_push`

### `SYN-003` - Deletion propagation is opt-in and recoverable

**Contract:** Local and remote deletions MUST be reported but not propagated by
default. With `--propagate-deletes`, a local deletion MAY move a Joplin note to
trash and a remote deletion MAY move the local file to quarantine. Sync MUST
never request permanent note deletion. Delete-versus-edit MUST conflict.

**Evidence:**

- [`test_local_delete_reported_not_propagated`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_local_delete_reported_not_propagated`
- [`test_local_delete_propagated_to_trash`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_local_delete_propagated_to_trash`
- [`test_remote_delete_reported_then_quarantined`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PullChangesTest::test_remote_delete_reported_then_quarantined`
- [`test_remote_deleted_local_edited`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::DeleteConflictTest::test_remote_deleted_local_edited`

### `SYN-004` - Divergence creates evidence without overwriting

**Contract:** Divergent edits MUST create one conflict bundle containing the
available base, local, and remote representations plus metadata and hashes.
Detection MUST leave both sides unchanged. Joplin's own conflict notes MUST be
reported and skipped rather than synchronized.

**Evidence:**

- [`test_divergent_edit_creates_conflict_without_overwrites`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_divergent_edit_creates_conflict_without_overwrites`
- [`test_joplin_conflict_notes_surfaced_and_skipped`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::JoplinConflictNoteTest::test_joplin_conflict_notes_surfaced_and_skipped`

### `SYN-005` - Conflict resolution is explicit and freshness-checked

**Contract:** Resolution MUST accept only take-local, take-remote, or a valid
explicit merged file. Both sides MUST be re-read before any resolution write;
changed evidence MUST produce concurrent-modification failure. Discard MUST
remove only the bundle and MAY allow later redetection.

**Evidence:**

- [`test_resolve_take_local`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_resolve_take_local`
- [`test_resolve_take_remote`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_resolve_take_remote`
- [`test_resolve_merged_file`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_resolve_merged_file`
- [`test_stale_resolution_refused_after_remote_change`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_stale_resolution_refused_after_remote_change`
- [`test_discard_conflict`](../../tests/integration/test_conflicts.py) - `tests/integration/test_conflicts.py::ConflictFlowTest::test_discard_conflict`

### `SYN-006` - Every operation guards, applies, verifies, then commits

**Contract:** Immediately before a write, the executor MUST re-read the
affected source and compare it with planned state. Drift MUST abort that
operation with exit `5` without overwriting either concurrent edit. The base
snapshot MUST update only after the intended post-state is verified.

**Evidence:**

- [`test_remote_change_between_plan_and_put`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RaceProtectionTest::test_remote_change_between_plan_and_put`
- [`test_local_change_between_plan_and_apply`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RaceProtectionTest::test_local_change_between_plan_and_apply`

### `SYN-007` - Ambiguous remote writes are settled without blind replay

**Contract:** A timed-out write MUST NOT be replayed automatically. The
executor MUST re-read remote state: an intended result counts as applied, an
unchanged pre-state is a partial failure safe to rerun, and any third state is
a concurrent modification.

**Evidence:**

- [`test_ambiguous_write_applied_is_detected`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RaceProtectionTest::test_ambiguous_write_applied_is_detected`
- [`test_ambiguous_write_not_applied_reports_failure`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RaceProtectionTest::test_ambiguous_write_not_applied_reports_failure`

### `SYN-008` - Locking and journals make interruption explicit

**Contract:** One exclusive cross-platform workspace lock MUST guard commands
that inspect or mutate shared state. Every mutating run MUST persist its plan
and per-operation status. An incomplete journal MUST block later mutations
until `recover` classifies each operation from verifiable current state without
replaying it.

**Evidence:**

- [`test_second_process_fails_clearly_while_locked`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::LockingIntegrationTest::test_second_process_fails_clearly_while_locked`
- [`test_incomplete_journal_blocks_mutations`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RecoveryTest::test_incomplete_journal_blocks_mutations`
- [`test_recover_settles_and_unblocks`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RecoveryTest::test_recover_settles_and_unblocks`
- [`test_recover_marks_actually_applied_ops`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::RecoveryTest::test_recover_marks_actually_applied_ops`

### `SYN-009` - State corruption and schema drift fail closed

**Contract:** Missing, corrupt, unsupported-newer, or un-migratable state MUST
fail as an invalid workspace rather than start from an empty base. Known older
schemas MUST migrate through the ordered migration chain.

**Evidence:**

- [`test_missing_db_requires_init`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_missing_db_requires_init`
- [`test_corrupt_database_detected`](../../tests/integration/test_races_recovery.py) - `tests/integration/test_races_recovery.py::CorruptStateTest::test_corrupt_database_detected`
- [`test_newer_schema_rejected`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_newer_schema_rejected`
- [`test_missing_migration_fails_cleanly`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_missing_migration_fails_cleanly`
