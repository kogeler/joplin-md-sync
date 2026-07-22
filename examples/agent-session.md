# Example agent session

A complete, real transcript shape of the canonical workflow (token value
elided; JSON abbreviated to the relevant fields).

```console
$ joplin-md-sync version --json
{"code": "OK", "exit_code": 0, "tool_version": "1.5.0", "protocol_version": 1, ...}

$ joplin-md-sync update-check --json
{"code": "UPDATE_CHECK_FAILED", "exit_code": 4, ...}   # offline CI runner — non-fatal

$ joplin-md-sync init --root ./notes --mode remote-first
initialized remote-first workspace at /work/notes

$ joplin-md-sync doctor --root ./notes --json
{"code": "OK", "exit_code": 0, "healthy": true, "checks": [
  {"check": "workspace", "ok": true}, {"check": "joplin_ping", "ok": true},
  {"check": "joplin_auth", "ok": true}, ...]}

$ joplin-md-sync pull --root ./notes --json
{"code": "OK", "exit_code": 0, "execution": {"applied": 42, "failed": 0}, ...}
```

The agent edits one existing note (below the header line) and creates one
new file `notes/Work/Standup notes.md` containing plain Markdown.

```console
$ joplin-md-sync diff --root ./notes --three-way --unified
=== LOCAL_MODIFIED Work/Kubernetes--17a35454.md
--- base/17a35454fbb34ee080e29fba9ee88730
+++ local/Work/Kubernetes--17a35454.md
@@ -3,0 +4 @@
+New troubleshooting section.

=== LOCAL_NEW Work/Standup notes.md  (no metadata header; will be adopted on push)

$ joplin-md-sync push --root ./notes --dry-run --json
{"code": "PENDING_ACTIONS", "exit_code": 1, "planned_operations": [
  {"op_id": "op-0001", "kind": "push_create_remote", "path": "Work/Standup notes.md"},
  {"op_id": "op-0002", "kind": "push_update_remote", "fields": ["body"],
   "path": "Work/Kubernetes--17a35454.md"}]}

$ joplin-md-sync push --root ./notes --json
{"code": "OK", "exit_code": 0, "execution": {"applied": 2, "failed": 0}, ...}

$ joplin-md-sync status --root ./notes --json
{"code": "OK", "exit_code": 0, "summary": {"local_modified": 0, "local_new": 0, ...}}
```

Conflict path — someone edited the same note in Joplin meanwhile:

```console
$ joplin-md-sync sync --root ./notes --json
{"code": "CONFLICTS_PRESENT", "exit_code": 2, "open_conflicts": 1, ...}

$ joplin-md-sync conflicts list --root ./notes --json
{"conflicts": [{"conflict_id": "9c2f1a77b3e04d21", "category": "divergent_edit",
  "path": "Work/Kubernetes--17a35454.md", "note_id": "17a35454..."}], ...}

$ joplin-md-sync conflicts resolve 9c2f1a77b3e04d21 --take-local --root ./notes --json
{"code": "OK", "exit_code": 0, "action": "local content pushed to Joplin", ...}
```
