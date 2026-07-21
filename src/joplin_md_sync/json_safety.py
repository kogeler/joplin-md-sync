"""Small transport-level guards applied before JSON decoding."""

from __future__ import annotations

MAX_JSON_NESTING = 128


def json_nesting_exceeds(data: bytes, *, maximum: int = MAX_JSON_NESTING) -> bool:
    """Return whether JSON containers exceed a bounded lexical nesting depth."""
    depth = 0
    in_string = False
    escaped = False
    for value in data:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in (ord("["), ord("{")):
            depth += 1
            if depth > maximum:
                return True
        elif value in (ord("]"), ord("}")) and depth:
            depth -= 1
    return False
