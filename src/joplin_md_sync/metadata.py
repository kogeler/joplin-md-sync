"""Managed Markdown metadata header: parse and emit.

Format (docs/WORKSPACE_FORMAT.md):

    <!-- joplin-md-sync: {"id":"<32 hex>","schema":1,"tags":["a","b"],"title":"..."} -->
    <blank line>
    <exact Joplin Markdown body>

* The header is exactly one line and must be the first line of the file.
* JSON keys are emitted in sorted order with compact separators.
* A missing ``id`` key marks a new local note.
* A malformed header makes the file invalid and blocks push for that file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from joplin_md_sync import NOTE_METADATA_SCHEMA_VERSION
from joplin_md_sync.canonical import canonicalize_body, canonicalize_tags

HEADER_PREFIX = "<!-- joplin-md-sync: "
HEADER_SUFFIX = " -->"

_NOTE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class ParsedNoteFile:
    note_id: str | None
    title: str
    tags: tuple[str, ...]
    body: str  # canonicalized (LF line endings)


class MetadataError(ValueError):
    """The metadata header is malformed. The message states why."""


def is_valid_note_id(value: str) -> bool:
    return bool(_NOTE_ID_RE.match(value))


def serialize_header(note_id: str | None, title: str, tags: tuple[str, ...]) -> str:
    """Produce the deterministic single-line metadata comment."""
    meta: dict[str, object] = {
        "schema": NOTE_METADATA_SCHEMA_VERSION,
        "tags": list(canonicalize_tags(tags)),
        "title": title,
    }
    if note_id is not None:
        meta["id"] = note_id
    payload = json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # "-->" inside a JSON string would terminate the HTML comment early;
    # escape the ">" (valid JSON string escape, round-trips via json.loads).
    payload = payload.replace("-->", "--\\u003e")
    return f"{HEADER_PREFIX}{payload}{HEADER_SUFFIX}"


def emit_note_file(note_id: str | None, title: str, tags: tuple[str, ...], body: str) -> str:
    """Full canonical file content: header, one blank line, exact body."""
    return serialize_header(note_id, title, tags) + "\n\n" + canonicalize_body(body)


def parse_note_file(text: str) -> ParsedNoteFile:
    """Parse a managed file. Raises MetadataError on a malformed header.

    Returns the canonicalized body. A file with no header at all raises
    MetadataError with ``no_header=True``-style message; callers distinguish
    "no header" from "broken header" via :func:`has_header`.
    """
    text = canonicalize_body(text)
    first_line, _, rest = text.partition("\n")
    if not first_line.startswith(HEADER_PREFIX):
        raise MetadataError("missing joplin-md-sync metadata header on the first line")
    if not first_line.endswith(HEADER_SUFFIX):
        raise MetadataError("metadata header is not a single-line HTML comment")
    payload = first_line[len(HEADER_PREFIX) : -len(HEADER_SUFFIX)]
    try:
        meta = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MetadataError(f"metadata header is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise MetadataError("metadata header must be a JSON object")

    schema = meta.get("schema")
    if schema != NOTE_METADATA_SCHEMA_VERSION:
        raise MetadataError(f"unsupported metadata schema {schema!r}, expected {NOTE_METADATA_SCHEMA_VERSION}")

    note_id = meta.get("id")
    if note_id is not None:
        if not isinstance(note_id, str) or not is_valid_note_id(note_id):
            raise MetadataError("metadata 'id' must be a 32-character lowercase hex string")

    title = meta.get("title")
    if not isinstance(title, str):
        raise MetadataError("metadata 'title' must be a string")

    tags_raw = meta.get("tags", [])
    if not isinstance(tags_raw, list) or not all(isinstance(t, str) for t in tags_raw):
        raise MetadataError("metadata 'tags' must be an array of strings")

    unknown = set(meta) - {"id", "schema", "tags", "title"}
    if unknown:
        raise MetadataError(f"unknown metadata keys: {', '.join(sorted(unknown))}")

    # Body: header line, then exactly one blank separator line, then the body.
    if rest == "":
        body = ""
    elif rest.startswith("\n"):
        body = rest[1:]
    else:
        raise MetadataError("metadata header must be followed by one blank line")

    return ParsedNoteFile(
        note_id=note_id,
        title=title,
        tags=canonicalize_tags(tags_raw),
        body=body,
    )


def has_header(text: str) -> bool:
    """True when the first line looks like a managed metadata header."""
    first_line = canonicalize_body(text).partition("\n")[0]
    return first_line.startswith(HEADER_PREFIX)
