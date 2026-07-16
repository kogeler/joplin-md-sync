"""Operation journal: crash-safe record of every mutating run.

Model: scan -> compare -> immutable plan -> persist journal -> apply ->
verify each op -> commit state -> mark journal complete.

The journal file is rewritten atomically after every status change, so a
crash at any point leaves a readable record. An incomplete journal blocks
new mutating commands until ``recover`` handles it.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from joplin_md_sync import __version__
from joplin_md_sync.errors import RecoveryRequiredError
from joplin_md_sync.models import PlanOperation
from joplin_md_sync.state import StateStore
from joplin_md_sync.workspace import Workspace, write_file_atomic

OP_PLANNED = "planned"
OP_APPLIED = "applied"  # applied and verified
OP_SKIPPED = "skipped"
OP_FAILED = "failed"

RUN_IN_PROGRESS = "in-progress"
RUN_COMPLETE = "complete"
RUN_FAILED = "failed"
RUN_RECOVERED = "recovered"


class Journal:
    def __init__(self, workspace: Workspace, store: StateStore, command: str) -> None:
        self.run_id = uuid.uuid4().hex
        self.workspace = workspace
        self.store = store
        self.command = command
        self.path = workspace.journal_dir / f"{self.run_id}.json"
        self._doc: dict[str, Any] = {
            "run_id": self.run_id,
            "tool_version": __version__,
            "command": command,
            "started_time": int(time.time() * 1000),
            "status": RUN_IN_PROGRESS,
            "operations": [],
        }

    # --- lifecycle -------------------------------------------------------

    def begin(self, operations: list[PlanOperation], *, input_summary: dict[str, object]) -> None:
        """Persist the immutable plan before anything is applied."""
        self._doc["input"] = input_summary
        self._doc["operations"] = [
            {**op.to_json(), "status": OP_PLANNED,
             "expected_local_hash": op.expected_local_hash,
             "expected_remote_hash": op.expected_remote_hash}
            for op in operations
        ]
        self._flush()
        self.store.record_run(
            run_id=self.run_id, command=self.command, journal_path=str(self.path)
        )

    def mark(self, op_id: str, status: str, detail: str = "") -> None:
        for op in self._doc["operations"]:
            if op["op_id"] == op_id:
                op["status"] = status
                if detail:
                    op["detail_result"] = detail
                break
        self._flush()

    def finish(self, status: str) -> None:
        self._doc["status"] = status
        self._doc["finished_time"] = int(time.time() * 1000)
        self._flush()
        self.store.finish_run(self.run_id, status)

    def _flush(self) -> None:
        write_file_atomic(self.path, json.dumps(self._doc, indent=2, sort_keys=True) + "\n")


def load_journal(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_no_incomplete_runs(store: StateStore) -> None:
    """Raise RecoveryRequiredError when a previous run did not complete."""
    runs = store.incomplete_runs()
    if runs:
        ids = ", ".join(row["run_id"] for row in runs)
        raise RecoveryRequiredError(
            f"previous run(s) did not complete: {ids}; "
            "run 'joplin-md-sync recover --root PATH' before any mutating command",
            details={"run_ids": [row["run_id"] for row in runs]},
        )


def _op_was_applied(op: dict[str, Any], store: StateStore) -> bool | None:
    """Decide from current state whether a journaled op reached its post-state.

    Base snapshots are committed strictly before the journal marks an op
    applied, so the base state is the reliable witness. Returns None when the
    outcome cannot be determined safely.
    """
    from joplin_md_sync import models  # local import to avoid a cycle

    kind = str(op.get("kind", ""))
    note_id = op.get("note_id")
    expected_local = op.get("expected_local_hash")
    expected_remote = op.get("expected_remote_hash")

    def base_combined() -> str | None:
        if not isinstance(note_id, str):
            return None
        base = store.get_note(note_id)
        return base.hashes.combined if base else None

    if kind in (models.OP_PULL_UPDATE_LOCAL, models.OP_PULL_CREATE_LOCAL):
        return base_combined() == expected_remote if expected_remote else None
    if kind in (models.OP_PUSH_UPDATE_REMOTE, models.OP_REBASE, models.OP_ADOPT_BASE):
        if op.get("folder_id") and not note_id:
            return None
        return base_combined() == expected_local if expected_local else None
    if kind == models.OP_PUSH_CREATE_REMOTE:
        if not isinstance(expected_local, str):
            return None
        return any(
            note.hashes.combined == expected_local for note in store.all_notes().values()
        )
    if kind in (models.OP_PUSH_DELETE_REMOTE, models.OP_PULL_DELETE_LOCAL, models.OP_DROP_BASE):
        if isinstance(note_id, str):
            return store.get_note(note_id) is None
        return None
    if kind == models.OP_CREATE_CONFLICT:
        if isinstance(note_id, str):
            return store.open_conflict_for_note(note_id) is not None
        return None
    if kind == models.OP_NORMALIZE_LOCAL_PATH:
        if isinstance(note_id, str):
            base = store.get_note(note_id)
            return base is not None and base.rel_path == op.get("new_path")
        return None
    if kind in (models.OP_PULL_CREATE_DIR, models.OP_PUSH_CREATE_FOLDER,
                models.OP_PULL_UPDATE_DIR, models.OP_PUSH_UPDATE_FOLDER):
        folder_id = op.get("folder_id")
        if isinstance(folder_id, str):
            return folder_id in store.all_folders()
        return None
    return None


def recover_incomplete_runs(workspace: Workspace, store: StateStore) -> dict[str, Any]:
    """Conservatively settle every incomplete journal.

    Each non-terminal operation is checked against current state: marked
    applied when its post-state is verifiably present, otherwise marked
    skipped with an instruction to rerun. Nothing is re-applied here — the
    next pull/push/sync re-plans from consistent state.
    """
    runs_report: list[dict[str, Any]] = []
    for row in store.incomplete_runs():
        run_id = row["run_id"]
        journal_path = Path(row["journal_path"])
        ops_report: list[dict[str, Any]] = []
        doc: dict[str, Any] | None = None
        if journal_path.is_file():
            doc = load_journal(journal_path)
            for op in doc.get("operations", []):
                if op.get("status") in (OP_APPLIED, OP_SKIPPED, OP_FAILED):
                    continue
                applied = _op_was_applied(op, store)
                if applied:
                    op["status"] = OP_APPLIED
                    op["detail_result"] = "verified as applied during recovery"
                else:
                    op["status"] = OP_SKIPPED
                    op["detail_result"] = (
                        "not applied (or unverifiable); rerun the original command"
                    )
                ops_report.append(
                    {"op_id": op.get("op_id"), "kind": op.get("kind"), "status": op["status"]}
                )
            doc["status"] = RUN_RECOVERED
            doc["recovered_time"] = int(time.time() * 1000)
            write_file_atomic(journal_path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        store.finish_run(run_id, RUN_RECOVERED)
        runs_report.append(
            {
                "run_id": run_id,
                "command": row["command"],
                "journal": str(journal_path),
                "journal_found": doc is not None,
                "operations_settled": ops_report,
            }
        )

    # Remove stray temp files from interrupted atomic writes.
    removed_tmp: list[str] = []
    for tmp in workspace.root.rglob(".jms-tmp-*"):
        try:
            tmp.unlink()
            removed_tmp.append(str(tmp.relative_to(workspace.root).as_posix()))
        except OSError:
            pass

    return {"recovered_runs": runs_report, "removed_temp_files": removed_tmp}
