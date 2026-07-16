"""Resource download for agent inspection.

``resources pull`` downloads every resource referenced from managed Markdown
files into ``.joplin-sync/resources/<resource-id>[.ext]``. Markdown
``:/resource-id`` links are never rewritten; binary upload is out of scope
for v1.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from joplin_md_sync.api import JoplinClient
from joplin_md_sync.workspace import LocalScan, Workspace, write_bytes_atomic

_RESOURCE_LINK_RE = re.compile(r"\(:/([0-9a-f]{32})\)|]\(:/([0-9a-f]{32})|:/([0-9a-f]{32})")


def referenced_resource_ids(scan: LocalScan) -> list[str]:
    ids: set[str] = set()
    for note in scan.notes:
        for match in _RESOURCE_LINK_RE.finditer(note.body):
            ids.add(next(g for g in match.groups() if g))
    return sorted(ids)


def _target_name(resource_id: str, meta: dict[str, Any]) -> str:
    ext = ""
    filename = meta.get("filename") or ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[1]
    elif meta.get("mime"):
        ext = mimetypes.guess_extension(meta["mime"]) or ""
    return f"{resource_id}{ext}"


def pull_resources(
    ws: Workspace,
    client: JoplinClient,
    scan: LocalScan,
    *,
    known_note_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Download referenced resources.

    Joplin uses the same ``:/id`` syntax for note-to-note links and resource
    links; ids that belong to known notes are skipped as note links instead
    of being misreported as missing resources.
    """
    downloaded: list[dict[str, Any]] = []
    skipped: list[str] = []
    missing: list[str] = []
    note_links: list[str] = []
    for resource_id in referenced_resource_ids(scan):
        if resource_id in known_note_ids:
            note_links.append(resource_id)
            continue
        existing = list(ws.resources_dir.glob(f"{resource_id}*"))
        if existing:
            skipped.append(resource_id)
            continue
        meta = client.get_resource(resource_id)
        if meta is None:
            missing.append(resource_id)
            continue
        data = client.get_resource_file(resource_id)
        target: Path = ws.resources_dir / _target_name(resource_id, meta)
        write_bytes_atomic(target, data)
        downloaded.append(
            {
                "resource_id": resource_id,
                "path": str(target.relative_to(ws.root).as_posix()),
                "bytes": len(data),
                "mime": meta.get("mime"),
            }
        )
    return {
        "downloaded": downloaded,
        "already_present": skipped,
        "missing": missing,
        "note_links_skipped": note_links,
        "resources_dir": str(ws.resources_dir.relative_to(ws.root).as_posix()),
    }
