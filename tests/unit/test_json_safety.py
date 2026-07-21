"""Transport-level JSON safety tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.json_safety import MAX_JSON_NESTING, json_nesting_exceeds


def test_json_nesting_limit_is_independent_of_parser_recursion_behavior() -> None:
    assert not json_nesting_exceeds(b"[" * MAX_JSON_NESTING + b"]" * MAX_JSON_NESTING)
    assert json_nesting_exceeds(
        b"[" * (MAX_JSON_NESTING + 1) + b"]" * (MAX_JSON_NESTING + 1)
    )


def test_json_delimiters_inside_strings_do_not_count_as_nesting() -> None:
    payload = b'{"text":"[[[{{{\\\"still a string]}}"}'
    assert not json_nesting_exceeds(payload, maximum=1)
