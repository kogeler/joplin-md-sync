"""Command-line interface: argparse tree, JSON envelope, stable exit codes.

Contract (docs/CLI.md, AGENTS.md):
* ``--json`` output is deterministic UTF-8 on stdout, free of logs;
* logs and diagnostics go to stderr (and optionally ``--log-file``);
* exit codes are stable (see errors.py);
* the Joplin token never appears in any output.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
from pathlib import Path
from typing import Any

from joplin_md_sync import (
    OUTPUT_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    REPOSITORY_URL,
    STATE_SCHEMA_VERSION,
    __version__,
    errors,
    models,
)
from joplin_md_sync.api import JoplinClient
from joplin_md_sync.canonical import canonicalize_tags
from joplin_md_sync.config import build_client, resolve_token
from joplin_md_sync.diff import (
    filter_note,
    items_json,
    name_status_lines,
    snapshot_from_base,
    summary_counts,
    unified_output,
)
from joplin_md_sync.errors import (
    EXIT_CONFLICTS,
    EXIT_DIFF,
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_OUTDATED,
    JoplinSyncError,
    UnsafeOperationError,
    WorkspaceError,
)
from joplin_md_sync.journal import Journal, check_no_incomplete_runs, recover_incomplete_runs
from joplin_md_sync.locking import WorkspaceLock
from joplin_md_sync.metadata import (
    MetadataError,
    emit_note_file,
    has_header,
    parse_note_file,
    serialize_header,
)
from joplin_md_sync.planner import build_plan, classify
from joplin_md_sync.sync import Executor, build_remote_snapshot, finalize_run
from joplin_md_sync.workspace import Workspace, write_file_atomic

log = logging.getLogger("joplin_md_sync")

_REDACT_TOKENS: list[str] = []


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for token in _REDACT_TOKENS:
            if token and token in msg:
                record.msg = msg.replace(token, "***")
                record.args = ()
        return True


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")
    p.add_argument("--verbose", action="store_true", help="debug logging on stderr")
    p.add_argument("--quiet", action="store_true", help="errors only on stderr")
    p.add_argument("--log-file", metavar="PATH", help="also write debug logs to PATH")


def _add_conn_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base-url", metavar="URL", help="Joplin API base URL (e.g. http://127.0.0.1:41184)")
    p.add_argument("--port", type=int, metavar="PORT", help="Joplin API port on 127.0.0.1")
    p.add_argument("--token-file", metavar="PATH", help="file containing the Joplin token")
    p.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS", help="HTTP timeout (default 30)")
    p.add_argument(
        "--allow-remote-api", action="store_true",
        help="allow a non-loopback Joplin API address (off by default)",
    )


def _add_root_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default=".", metavar="PATH", help="workspace root (default: current directory)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="joplin-md-sync",
        description="Safe two-way synchronization between Joplin notes and a local Markdown workspace.",
    )
    parser.add_argument("--version", action="version", version=f"joplin-md-sync {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("version", help="report tool and contract versions")
    _add_output_args(p)

    p = sub.add_parser("capabilities", help="report supported features and commands")
    _add_output_args(p)

    p = sub.add_parser("update-check", help="check GitHub releases for a newer version")
    _add_output_args(p)
    p.add_argument("--include-prerelease", action="store_true")
    p.add_argument("--offline", action="store_true", help="skip the network check")

    p = sub.add_parser("init", help="initialize a workspace")
    _add_output_args(p)
    _add_root_arg(p)
    p.add_argument(
        "--mode", choices=("remote-first", "local-first"), default="remote-first",
        help="remote-first (default): Joplin is the initial source of truth",
    )

    p = sub.add_parser("doctor", help="diagnose workspace and Joplin connectivity")
    _add_output_args(p)
    _add_conn_args(p)
    _add_root_arg(p)
    p.add_argument("--offline", action="store_true", help="skip Joplin connectivity checks")

    p = sub.add_parser("status", help="offline workspace status against the base snapshot")
    _add_output_args(p)
    _add_root_arg(p)

    for name, help_text in (
        ("pull", "apply remote changes to the local workspace"),
        ("push", "apply local changes to Joplin"),
        ("sync", "pull and push in one safe run"),
    ):
        p = sub.add_parser(name, help=help_text)
        _add_output_args(p)
        _add_conn_args(p)
        _add_root_arg(p)
        p.add_argument("--dry-run", action="store_true", help="plan only; mutate nothing")
        p.add_argument(
            "--propagate-deletes", action="store_true",
            help="apply deletions across sides (local quarantine / Joplin trash)",
        )

    p = sub.add_parser("diff", help="compare local, remote, and base states (never mutates)")
    _add_output_args(p)
    _add_conn_args(p)
    _add_root_arg(p)
    p.add_argument("--summary", action="store_true", help="summary counts (default)")
    p.add_argument("--name-status", action="store_true", help="one STATUS<TAB>path line per item")
    p.add_argument("--unified", action="store_true", help="unified body diffs")
    p.add_argument("--three-way", action="store_true", help="base->local and base->remote diffs")
    p.add_argument("--against", choices=("remote", "base"), default="remote")
    p.add_argument("--note", metavar="NOTE_ID_OR_PATH", help="limit to one note")
    p.add_argument("--exit-code", action="store_true", help="exit 1 on differences, 2 on conflicts")
    p.add_argument("--offline", action="store_true", help="compare against the base only")

    p = sub.add_parser("recover", help="settle incomplete journals from interrupted runs")
    _add_output_args(p)
    _add_root_arg(p)

    p = sub.add_parser("conflicts", help="list, inspect, and resolve conflicts")
    csub = p.add_subparsers(dest="conflicts_command", required=True, metavar="SUBCOMMAND")
    cp = csub.add_parser("list", help="list open conflicts")
    _add_output_args(cp)
    _add_root_arg(cp)
    cp = csub.add_parser("show", help="show one conflict bundle")
    _add_output_args(cp)
    _add_root_arg(cp)
    cp.add_argument("conflict_id")
    cp = csub.add_parser("resolve", help="resolve one conflict")
    _add_output_args(cp)
    _add_conn_args(cp)
    _add_root_arg(cp)
    cp.add_argument("conflict_id")
    group = cp.add_mutually_exclusive_group(required=True)
    group.add_argument("--take-local", action="store_true")
    group.add_argument("--take-remote", action="store_true")
    group.add_argument("--merged-file", metavar="PATH")
    cp = csub.add_parser("discard", help="discard a conflict bundle")
    _add_output_args(cp)
    _add_root_arg(cp)
    cp.add_argument("conflict_id")

    p = sub.add_parser("note", help="safe metadata editing and validation")
    nsub = p.add_subparsers(dest="note_command", required=True, metavar="SUBCOMMAND")
    np_ = nsub.add_parser("set-title", help="set the title in a managed file header")
    _add_output_args(np_)
    np_.add_argument("path")
    np_.add_argument("title")
    np_ = nsub.add_parser("set-tags", help="set the tag list in a managed file header")
    _add_output_args(np_)
    np_.add_argument("path")
    np_.add_argument("tags", nargs="*", metavar="TAG")
    np_ = nsub.add_parser("validate", help="validate a managed Markdown file")
    _add_output_args(np_)
    np_.add_argument("path")

    p = sub.add_parser("resources", help="download referenced resources")
    rsub = p.add_subparsers(dest="resources_command", required=True, metavar="SUBCOMMAND")
    rp = rsub.add_parser("pull", help="download resources referenced by managed notes")
    _add_output_args(rp)
    _add_conn_args(rp)
    _add_root_arg(rp)

    return parser


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _setup_logging(args: argparse.Namespace) -> None:
    root = logging.getLogger("joplin_md_sync")
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    stderr = logging.StreamHandler(sys.stderr)
    if getattr(args, "quiet", False):
        stderr.setLevel(logging.ERROR)
    elif getattr(args, "verbose", False):
        stderr.setLevel(logging.DEBUG)
    else:
        stderr.setLevel(logging.WARNING)
    stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    stderr.addFilter(_RedactionFilter())
    root.addHandler(stderr)
    log_file = getattr(args, "log_file", None)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        fh.addFilter(_RedactionFilter())
        root.addHandler(fh)


def _client_for(args: argparse.Namespace, config: dict[str, Any] | None = None) -> JoplinClient:
    token = resolve_token(getattr(args, "token_file", None))
    _REDACT_TOKENS.append(token)
    workspace_base_url: str | None = None
    if config:
        raw = config.get("base_url")
        workspace_base_url = raw if isinstance(raw, str) else None
    return build_client(
        cli_base_url=getattr(args, "base_url", None),
        cli_port=getattr(args, "port", None),
        token_file=getattr(args, "token_file", None),
        workspace_base_url=workspace_base_url,
        allow_remote=getattr(args, "allow_remote_api", False),
        timeout=getattr(args, "timeout", 30.0),
    )


def _distribution() -> str:
    module_path = str(Path(__file__))
    if ".pyz" in module_path or ".zip" in module_path:
        return "zipapp"
    try:
        import importlib.metadata

        importlib.metadata.distribution("joplin-md-sync")
        return "wheel"
    except Exception:
        return "source"


class CommandOutput:
    def __init__(
        self,
        exit_code: int,
        code: str,
        payload: dict[str, Any] | None = None,
        text: list[str] | None = None,
        workspace: str | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.code = code
        self.payload = payload or {}
        self.text = text or []
        self.workspace = workspace


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_version(args: argparse.Namespace) -> CommandOutput:
    payload = {
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "protocol_version": PROTOCOL_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "repository": REPOSITORY_URL,
        "build_commit": None,
        "distribution": _distribution(),
    }
    text = [f"joplin-md-sync {__version__} (python {payload['python_version']}, {payload['distribution']})"]
    return CommandOutput(EXIT_OK, errors.CODE_OK, payload, text)


def cmd_capabilities(args: argparse.Namespace) -> CommandOutput:
    payload = {
        "tool_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "platform": {"system": platform.system(), "python": platform.python_version()},
        "commands": [
            "version", "capabilities", "update-check", "init", "doctor", "status",
            "pull", "push", "sync", "diff", "recover",
            "conflicts list", "conflicts show", "conflicts resolve", "conflicts discard",
            "note set-title", "note set-tags", "note validate", "resources pull",
        ],
        "features": {
            "propagate_deletes_flag": True,
            "automatic_text_merge": False,
            "resource_download": True,
            "resource_upload": False,
            "events_optimization": False,
            "permanent_deletion": False,
        },
        "exit_codes": {
            "0": "ok / no differences", "1": "differences or pending actions",
            "2": "unresolved conflicts", "3": "invalid workspace or managed file",
            "4": "API unavailable or auth failed", "5": "concurrent modification / lock busy",
            "6": "partial operation / recovery required", "7": "unsafe operation blocked",
            "8": "tool version outdated", "9": "internal failure",
        },
    }
    return CommandOutput(EXIT_OK, errors.CODE_OK, payload, ["capabilities reported"])


def cmd_update_check(args: argparse.Namespace) -> CommandOutput:
    if args.offline:
        payload = {"checked": False, "reason": "offline mode requested", "current_version": __version__}
        return CommandOutput(EXIT_OK, "UPDATE_CHECK_SKIPPED", payload, ["update check skipped (--offline)"])
    from joplin_md_sync.update_check import check_for_update

    result = check_for_update(include_prerelease=args.include_prerelease)
    result["checked"] = True
    if result["outdated"]:
        text = [
            f"outdated: {result['current_version']} -> {result['latest_version']}",
            f"update: {result['update_command']}",
        ]
        return CommandOutput(EXIT_OUTDATED, errors.CODE_OUTDATED, result, text)
    return CommandOutput(
        EXIT_OK, errors.CODE_OK, result, [f"up to date: {result['current_version']}"]
    )


def cmd_init(args: argparse.Namespace) -> CommandOutput:
    root = Path(args.root)
    if root.is_dir():
        preexisting = [
            str(p.relative_to(root).as_posix())
            for p in sorted(root.rglob("*.md"))
            if ".joplin-sync" not in p.parts
        ]
    else:
        preexisting = []
    if args.mode == "remote-first" and preexisting:
        raise UnsafeOperationError(
            f"{len(preexisting)} Markdown file(s) already exist under {root}; "
            "remote-first init refuses to adopt them — use --mode local-first",
            details={"existing_files": preexisting[:50]},
        )
    ws = Workspace.create(root, mode=args.mode)
    payload: dict[str, Any] = {"mode": args.mode, "root": str(ws.root)}
    if args.mode == "local-first":
        scan = ws.scan()
        payload["adoptable_notes"] = len(scan.notes)
        payload["invalid_files"] = [
            {"path": inv.rel_path, "reason": inv.reason} for inv in scan.invalid
        ]
        payload["note"] = (
            "files without a Joplin id will be pushed as new notes; "
            "run 'push --dry-run' first (required before the first real push)"
        )
    text = [f"initialized {args.mode} workspace at {ws.root}"]
    return CommandOutput(EXIT_OK, errors.CODE_OK, payload, text, workspace=str(ws.root))


def cmd_doctor(args: argparse.Namespace) -> CommandOutput:
    checks: list[dict[str, Any]] = []
    exit_code = EXIT_OK
    code = errors.CODE_OK

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python_version", sys.version_info >= (3, 13), platform.python_version())

    ws: Workspace | None = None
    store = None
    try:
        ws = Workspace.load(Path(args.root))
        config = ws.read_config()
        add("workspace", True, str(ws.root))
        try:
            store = ws.open_state()
            add("state_database", True, "integrity ok")
        except WorkspaceError as exc:
            add("state_database", False, str(exc))
            exit_code, code = errors.EXIT_INVALID_WORKSPACE, errors.CODE_INVALID_WORKSPACE
    except WorkspaceError as exc:
        add("workspace", False, str(exc))
        exit_code, code = errors.EXIT_INVALID_WORKSPACE, errors.CODE_INVALID_WORKSPACE
        config = None

    if ws is not None and store is not None:
        runs = store.incomplete_runs()
        add(
            "incomplete_runs", not runs,
            "none" if not runs else f"{len(runs)} incomplete run(s); run 'recover'",
        )
        if runs and exit_code == EXIT_OK:
            exit_code, code = errors.EXIT_PARTIAL, errors.CODE_RECOVERY_REQUIRED
        conflicts = store.open_conflicts()
        add(
            "open_conflicts", not conflicts,
            "none" if not conflicts else f"{len(conflicts)} open conflict(s); see 'conflicts list'",
        )
        if conflicts and exit_code == EXIT_OK:
            exit_code, code = EXIT_CONFLICTS, errors.CODE_CONFLICTS_PRESENT
        scan = ws.scan()
        add(
            "invalid_local_files", not scan.invalid,
            "none" if not scan.invalid else "; ".join(f"{i.rel_path}: {i.reason}" for i in scan.invalid[:10]),
        )
        if scan.invalid and exit_code == EXIT_OK:
            exit_code, code = errors.EXIT_INVALID_WORKSPACE, errors.CODE_INVALID_LOCAL_FILE
        try:
            lock = WorkspaceLock(ws.lock_path)
            lock.acquire()
            lock.release()
            add("workspace_lock", True, "free")
        except JoplinSyncError:
            add("workspace_lock", False, "held by another process")
            if exit_code == EXIT_OK:
                exit_code, code = errors.EXIT_CONCURRENT, errors.CODE_WORKSPACE_LOCKED

    if not args.offline:
        try:
            client = _client_for(args, config if ws else None)
            ok = client.ping()
            add("joplin_ping", ok, client.base_url if ok else "unexpected ping response")
            client.list_notes()  # cheap auth check via first page
            add("joplin_auth", True, "token accepted")
        except JoplinSyncError as exc:
            add("joplin_api", False, str(exc))
            if exit_code == EXIT_OK:
                exit_code, code = exc.exit_code, exc.code
    if store is not None:
        store.close()

    payload = {"checks": checks, "healthy": exit_code == EXIT_OK}
    text = [f"{'ok ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}" for c in checks]
    return CommandOutput(exit_code, code, payload, text, workspace=str(ws.root) if ws else None)


def cmd_status(args: argparse.Namespace) -> CommandOutput:
    ws = Workspace.load(Path(args.root))
    with WorkspaceLock(ws.lock_path):
        store = ws.open_state()
        try:
            base_notes = store.all_notes()
            base_folders = store.all_folders()
            scan = ws.scan()
            open_ids = frozenset(row["note_id"] for row in store.open_conflicts())
            classification = classify(
                base_notes, base_folders, scan, snapshot_from_base(base_notes, base_folders),
                open_conflict_note_ids=open_ids,
            )
            summary = summary_counts(classification)
            from joplin_md_sync.conflicts import list_conflicts

            conflicts = list_conflicts(store)
            runs = [dict(r) for r in store.incomplete_runs()]
            payload = {
                "summary": summary,
                "items": [i for i in items_json(classification, remote_known=False)
                          if i.get("status") != models.UNCHANGED],
                "open_conflicts": conflicts,
                "incomplete_runs": runs,
                "remote_state": "unknown (status is offline; use diff for a live comparison)",
                "tracked_notes": len(base_notes),
                "tracked_notebooks": len(base_folders),
            }
        finally:
            store.close()
    text = [
        f"tracked: {payload['tracked_notes']} notes in {payload['tracked_notebooks']} notebooks",
        f"local changes vs base: {summary['local_modified']} modified, "
        f"{summary['local_new']} new, {summary['local_deleted']} deleted",
        f"open conflicts: {len(conflicts)}; incomplete runs: {len(runs)}",
    ]
    exit_code = EXIT_OK
    code = errors.CODE_OK
    if runs:
        exit_code, code = errors.EXIT_PARTIAL, errors.CODE_RECOVERY_REQUIRED
    elif conflicts:
        exit_code, code = EXIT_CONFLICTS, errors.CODE_CONFLICTS_PRESENT
    return CommandOutput(exit_code, code, payload, text, workspace=str(ws.root))


def _sync_like(args: argparse.Namespace, direction: str) -> CommandOutput:
    ws = Workspace.load(Path(args.root))
    config = ws.read_config()
    with WorkspaceLock(ws.lock_path):
        store = ws.open_state()
        try:
            check_no_incomplete_runs(store)
            client = _client_for(args, config)
            scan = ws.scan()
            base_notes = store.all_notes()
            base_folders = store.all_folders()
            open_ids = frozenset(row["note_id"] for row in store.open_conflicts())
            snapshot = build_remote_snapshot(client, base_notes)
            classification = classify(
                base_notes, base_folders, scan, snapshot, open_conflict_note_ids=open_ids
            )
            plan = build_plan(
                classification, direction=direction,
                propagate_deletes=args.propagate_deletes,
            )
            summary = summary_counts(classification)
            conflict_count = summary["conflicts"] + len(open_ids)
            blocked_deletions = (
                0
                if args.propagate_deletes
                else summary["local_deleted"] + summary["remote_deleted"]
            )

            if args.dry_run:
                if direction in ("push", "sync") and config.get("mode") == "local-first":
                    config.setdefault("options", {})
                    if not config.get("local_first_dry_run_done"):
                        config["local_first_dry_run_done"] = True
                        ws.write_config(config)
                payload = {
                    "dry_run": True,
                    "summary": summary,
                    "planned_operations": [op.to_json() for op in plan],
                    "pending_deletions_not_propagated": blocked_deletions,
                }
                if conflict_count:
                    return CommandOutput(
                        EXIT_CONFLICTS, errors.CODE_CONFLICTS_PRESENT, payload,
                        [f"dry-run: {len(plan)} operation(s); {conflict_count} conflict(s)"],
                        workspace=str(ws.root),
                    )
                if plan or blocked_deletions:
                    return CommandOutput(
                        EXIT_DIFF, errors.CODE_PENDING_ACTIONS, payload,
                        [f"dry-run: {len(plan)} operation(s) pending"], workspace=str(ws.root),
                    )
                return CommandOutput(
                    EXIT_OK, errors.CODE_OK, payload, ["dry-run: nothing to do"],
                    workspace=str(ws.root),
                )

            if (
                direction in ("push", "sync")
                and config.get("mode") == "local-first"
                and not config.get("local_first_dry_run_done")
            ):
                raise UnsafeOperationError(
                    "this local-first workspace requires 'push --dry-run' before the "
                    "first real push"
                )

            journal = Journal(ws, store, direction)
            journal.begin(plan, input_summary={"summary": summary})
            executor = Executor(ws, store, client, scan, journal)
            report = executor.run(plan)
            options = config.get("options")
            retention = (
                int(options.get("backup_retention", 10)) if isinstance(options, dict) else 10
            )
            finalize_run(ws, store, journal, snapshot, backup_retention=retention)
            open_after = len(store.open_conflicts())

            payload = {
                "dry_run": False,
                "summary": summary,
                "execution": report.to_json(),
                "open_conflicts": open_after,
                "pending_deletions_not_propagated": blocked_deletions,
            }
            text = [
                f"{direction}: {report.applied} applied, {report.failed} failed, "
                f"{report.skipped} skipped; {open_after} open conflict(s)"
            ]
            if report.concurrent_failures:
                return CommandOutput(
                    errors.EXIT_CONCURRENT, errors.CODE_CONCURRENT_MODIFICATION,
                    payload, text, workspace=str(ws.root),
                )
            if report.failed:
                return CommandOutput(
                    errors.EXIT_PARTIAL, errors.CODE_PARTIAL_FAILURE, payload, text,
                    workspace=str(ws.root),
                )
            if open_after:
                return CommandOutput(
                    EXIT_CONFLICTS, errors.CODE_CONFLICTS_PRESENT, payload, text,
                    workspace=str(ws.root),
                )
            return CommandOutput(EXIT_OK, errors.CODE_OK, payload, text, workspace=str(ws.root))
        finally:
            store.close()


def cmd_diff(args: argparse.Namespace) -> CommandOutput:
    ws = Workspace.load(Path(args.root))
    config = ws.read_config()
    with WorkspaceLock(ws.lock_path):
        store = ws.open_state()
        try:
            base_notes = store.all_notes()
            base_folders = store.all_folders()
            scan = ws.scan()
            open_ids = frozenset(row["note_id"] for row in store.open_conflicts())
            if args.offline:
                snapshot = snapshot_from_base(base_notes, base_folders)
            else:
                client = _client_for(args, config)
                snapshot = build_remote_snapshot(client, base_notes)
            classification = classify(
                base_notes, base_folders, scan, snapshot, open_conflict_note_ids=open_ids
            )
        finally:
            store.close()

    if args.note:
        classification = filter_note(classification, args.note)
    summary = summary_counts(classification)
    remote_known = not args.offline

    payload: dict[str, Any] = {
        "summary": summary,
        "items": items_json(classification, remote_known=remote_known),
        "offline": args.offline,
        "against": "base" if args.offline else args.against,
    }
    text: list[str] = []
    if args.name_status:
        text.extend(name_status_lines(classification))
    if args.unified or args.three_way:
        rendered = unified_output(
            classification,
            against="base" if args.offline else args.against,
            three_way=args.three_way,
        )
        if rendered:
            text.append(rendered.rstrip("\n"))
        payload["unified"] = rendered
    if not text:
        text = [
            f"{k}: {v}" for k, v in summary.items() if k != "by_status" and v
        ] or ["no differences"]

    has_changes = bool(
        [i for i in classification.items if i.status != models.UNCHANGED]
        or classification.folder_items
        or classification.invalid
    )
    has_conflicts = summary["conflicts"] > 0
    if args.exit_code:
        if has_conflicts:
            return CommandOutput(EXIT_CONFLICTS, errors.CODE_CONFLICTS_PRESENT, payload, text, str(ws.root))
        if has_changes:
            return CommandOutput(EXIT_DIFF, errors.CODE_DIFF_FOUND, payload, text, str(ws.root))
    code = errors.CODE_CONFLICTS_PRESENT if has_conflicts else (
        errors.CODE_DIFF_FOUND if has_changes else errors.CODE_OK
    )
    return CommandOutput(EXIT_OK, code, payload, text, str(ws.root))


def cmd_recover(args: argparse.Namespace) -> CommandOutput:
    ws = Workspace.load(Path(args.root))
    with WorkspaceLock(ws.lock_path):
        store = ws.open_state()
        try:
            result = recover_incomplete_runs(ws, store)
        finally:
            store.close()
    runs = result["recovered_runs"]
    text = [f"recovered {len(runs)} run(s)"] if runs else ["nothing to recover"]
    return CommandOutput(EXIT_OK, errors.CODE_OK, dict(result), text, str(ws.root))


def cmd_conflicts(args: argparse.Namespace) -> CommandOutput:
    from joplin_md_sync import conflicts as conflicts_mod

    ws = Workspace.load(Path(args.root))
    sub = args.conflicts_command
    with WorkspaceLock(ws.lock_path):
        store = ws.open_state()
        try:
            if sub == "list":
                items = conflicts_mod.list_conflicts(store)
                payload = {"conflicts": items}
                text = [
                    f"{c['conflict_id']}  {c['category']}  {c['path'] or c['note_id']}"
                    for c in items
                ] or ["no open conflicts"]
                code = errors.CODE_CONFLICTS_PRESENT if items else errors.CODE_OK
                exit_code = EXIT_CONFLICTS if items else EXIT_OK
                return CommandOutput(exit_code, code, payload, text, str(ws.root))
            if sub == "show":
                shown = conflicts_mod.show_conflict(ws, store, args.conflict_id)
                text = [f"conflict {args.conflict_id}: {shown['metadata']['category']}"]
                return CommandOutput(EXIT_OK, errors.CODE_OK, shown, text, str(ws.root))
            if sub == "resolve":
                check_no_incomplete_runs(store)
                config = ws.read_config()
                client = _client_for(args, config)
                mode = (
                    "take-local" if args.take_local
                    else "take-remote" if args.take_remote
                    else "merged-file"
                )
                journal = Journal(ws, store, f"conflicts resolve {mode}")
                op = models.PlanOperation(
                    op_id="op-0001", kind="resolve_conflict", detail=f"{args.conflict_id} {mode}"
                )
                journal.begin([op], input_summary={"conflict_id": args.conflict_id, "mode": mode})
                try:
                    result = conflicts_mod.resolve_conflict(
                        ws, store, client, args.conflict_id,
                        mode=mode, merged_file=args.merged_file, run_id=journal.run_id,
                    )
                    journal.mark("op-0001", "applied", result.get("action", ""))
                    journal.finish("complete")
                except JoplinSyncError as exc:
                    journal.mark("op-0001", "failed", str(exc))
                    journal.finish("complete")
                    raise
                return CommandOutput(
                    EXIT_OK, errors.CODE_OK, result,
                    [f"conflict {args.conflict_id} resolved: {result['action']}"], str(ws.root),
                )
            if sub == "discard":
                result = conflicts_mod.discard_conflict(ws, store, args.conflict_id)
                return CommandOutput(
                    EXIT_OK, errors.CODE_OK, result,
                    [f"conflict {args.conflict_id} discarded"], str(ws.root),
                )
        finally:
            store.close()
    raise errors.InternalError(f"unknown conflicts subcommand: {sub}")


def cmd_note(args: argparse.Namespace) -> CommandOutput:
    path = Path(args.path)
    sub = args.note_command
    if not path.is_file():
        raise WorkspaceError(f"file not found: {path}")
    raw = path.read_text(encoding="utf-8")

    if sub == "validate":
        if not has_header(raw):
            detail = (
                "no metadata header; the file will be pushed as a NEW note "
                "(title from the file name)"
            )
            payload = {"path": str(path), "valid": True, "managed": False, "detail": detail}
            return CommandOutput(EXIT_OK, errors.CODE_OK, payload, [detail])
        try:
            parsed = parse_note_file(raw)
        except MetadataError as exc:
            payload = {"path": str(path), "valid": False, "managed": True, "detail": str(exc)}
            return CommandOutput(
                errors.EXIT_INVALID_WORKSPACE, errors.CODE_INVALID_LOCAL_FILE, payload,
                [f"INVALID: {exc}"],
            )
        payload = {
            "path": str(path), "valid": True, "managed": True,
            "note_id": parsed.note_id, "title": parsed.title, "tags": list(parsed.tags),
        }
        return CommandOutput(EXIT_OK, errors.CODE_OK, payload, ["valid managed file"])

    # set-title / set-tags rewrite the header atomically.
    if has_header(raw):
        try:
            parsed = parse_note_file(raw)
        except MetadataError as exc:
            raise WorkspaceError(f"cannot edit malformed managed file: {exc}") from exc
        note_id, title, tags, body = parsed.note_id, parsed.title, parsed.tags, parsed.body
    else:
        note_id, title, tags, body = None, path.stem, (), raw

    if sub == "set-title":
        title = args.title
    elif sub == "set-tags":
        tags = canonicalize_tags(args.tags)
    write_file_atomic(path, emit_note_file(note_id, title, tags, body))
    payload = {
        "path": str(path), "note_id": note_id, "title": title, "tags": list(tags),
        "header": serialize_header(note_id, title, tags),
    }
    return CommandOutput(EXIT_OK, errors.CODE_OK, payload, [f"updated header of {path}"])


def cmd_resources(args: argparse.Namespace) -> CommandOutput:
    from joplin_md_sync.resources import pull_resources

    ws = Workspace.load(Path(args.root))
    config = ws.read_config()
    with WorkspaceLock(ws.lock_path):
        client = _client_for(args, config)
        scan = ws.scan()
        store = ws.open_state()
        try:
            known_note_ids = frozenset(store.all_notes())
        finally:
            store.close()
        # Local ids also count as note links (e.g. notes not yet in the base).
        known_note_ids |= frozenset(n.note_id for n in scan.notes if n.note_id)
        result = pull_resources(ws, client, scan, known_note_ids=known_note_ids)
    text = [
        f"downloaded {len(result['downloaded'])}, already present "
        f"{len(result['already_present'])}, missing {len(result['missing'])}, "
        f"note links skipped {len(result['note_links_skipped'])}"
    ]
    return CommandOutput(EXIT_OK, errors.CODE_OK, result, text, str(ws.root))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

_HANDLERS = {
    "version": cmd_version,
    "capabilities": cmd_capabilities,
    "update-check": cmd_update_check,
    "init": cmd_init,
    "doctor": cmd_doctor,
    "status": cmd_status,
    "pull": lambda a: _sync_like(a, "pull"),
    "push": lambda a: _sync_like(a, "push"),
    "sync": lambda a: _sync_like(a, "sync"),
    "diff": cmd_diff,
    "recover": cmd_recover,
    "conflicts": cmd_conflicts,
    "note": cmd_note,
    "resources": cmd_resources,
}


def _emit(args: argparse.Namespace, command: str, out: CommandOutput) -> int:
    envelope: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "command": command,
        "success": out.exit_code in (EXIT_OK, EXIT_DIFF, EXIT_CONFLICTS, EXIT_OUTDATED),
        "exit_code": out.exit_code,
        "code": out.code,
        "tool_version": __version__,
        "workspace": out.workspace,
    }
    envelope.update(out.payload)
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    else:
        for line in out.text:
            sys.stdout.write(line + "\n")
    return out.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args)
    command = args.command
    try:
        out = _HANDLERS[command](args)
        return _emit(args, command, out)
    except JoplinSyncError as exc:
        out = CommandOutput(
            exc.exit_code, exc.code,
            {"error": _redact(str(exc)), "details": _jsonable(exc.details)},
            [f"error: {_redact(str(exc))}"],
        )
        return _emit(args, command, out)
    except Exception as exc:  # deterministic internal-error path
        log.exception("internal error")
        out = CommandOutput(
            EXIT_INTERNAL, errors.CODE_INTERNAL_ERROR,
            {"error": _redact(f"{type(exc).__name__}: {exc}")},
            [f"internal error: {_redact(f'{type(exc).__name__}: {exc}')}"],
        )
        return _emit(args, command, out)


def _redact(text: str) -> str:
    for token in _REDACT_TOKENS:
        if token:
            text = text.replace(token, "***")
    return text


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
