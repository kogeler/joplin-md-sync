"""Canonicalization and hashing.

The exact same canonicalization is used for hashing and for file emission,
so a pulled file re-hashes to the hash stored in the base snapshot.

Rules (docs/STATE_MODEL.md):
* line endings are normalized to ``\\n``;
* no trailing-whitespace stripping, no Markdown reformatting;
* no Unicode normalization (NFC/NFD differences are preserved verbatim);
* tags are lowercased (Joplin normalizes tag titles to lowercase) and
  compared as a sorted, deduplicated set.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from joplin_md_sync.models import NoteHashes


def canonicalize_body(text: str) -> str:
    """Normalize line endings to LF. Nothing else is changed."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonicalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Lowercase, strip, deduplicate, and sort a tag collection."""
    cleaned = {t.strip().lower() for t in tags if t.strip()}
    return tuple(sorted(cleaned))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _field_bytes(value: str) -> bytes:
    """Length-prefix a field so concatenated fields can never be ambiguous."""
    raw = value.encode("utf-8")
    return b"%d:%s" % (len(raw), raw)


def hash_text(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def hash_tags(tags: Iterable[str]) -> str:
    canonical = canonicalize_tags(tags)
    return _sha256(b"\x00".join(t.encode("utf-8") for t in canonical))


def note_hashes(title: str, body: str, tags: Iterable[str], parent_id: str) -> NoteHashes:
    """Compute the component hashes and the combined logical-note hash."""
    canonical_body = canonicalize_body(body)
    canonical_tags = canonicalize_tags(tags)
    combined = hashlib.sha256()
    combined.update(_field_bytes(title))
    combined.update(_field_bytes(canonical_body))
    combined.update(_field_bytes("\x00".join(canonical_tags)))
    combined.update(_field_bytes(parent_id))
    return NoteHashes(
        title=hash_text(title),
        body=hash_text(canonical_body),
        tags=hash_tags(canonical_tags),
        parent=hash_text(parent_id),
        combined=combined.hexdigest(),
    )


def changed_components(a: NoteHashes, b: NoteHashes) -> tuple[str, ...]:
    """List the logical components that differ between two hash sets."""
    out: list[str] = []
    if a.title != b.title:
        out.append("title")
    if a.body != b.body:
        out.append("body")
    if a.tags != b.tags:
        out.append("tags")
    if a.parent != b.parent:
        out.append("parent")
    return tuple(out)
