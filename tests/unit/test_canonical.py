import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.canonical import (
    canonicalize_body,
    canonicalize_tags,
    changed_components,
    note_hashes,
)


class CanonicalizeBodyTest(unittest.TestCase):
    def test_crlf_normalized_to_lf(self):
        self.assertEqual(canonicalize_body("a\r\nb\r\nc"), "a\nb\nc")

    def test_bare_cr_normalized(self):
        self.assertEqual(canonicalize_body("a\rb"), "a\nb")

    def test_lf_untouched(self):
        self.assertEqual(canonicalize_body("a\nb\n"), "a\nb\n")

    def test_trailing_whitespace_preserved(self):
        # Meaningful in Markdown (hard line breaks); must never be stripped.
        self.assertEqual(canonicalize_body("line  \nnext"), "line  \nnext")

    def test_unicode_not_normalized(self):
        composed = "café"
        decomposed = "café"
        self.assertNotEqual(canonicalize_body(composed), canonicalize_body(decomposed))


class CanonicalizeTagsTest(unittest.TestCase):
    def test_lowercased_sorted_deduplicated(self):
        self.assertEqual(canonicalize_tags(["B", "a", "b", " a "]), ("a", "b"))

    def test_empty_tags_dropped(self):
        self.assertEqual(canonicalize_tags(["", "  ", "x"]), ("x",))


class NoteHashesTest(unittest.TestCase):
    def test_deterministic(self):
        a = note_hashes("T", "body", ("x",), "p1")
        b = note_hashes("T", "body", ("x",), "p1")
        self.assertEqual(a, b)

    def test_crlf_and_lf_bodies_hash_equal(self):
        a = note_hashes("T", "a\r\nb", (), "p")
        b = note_hashes("T", "a\nb", (), "p")
        self.assertEqual(a.body, b.body)
        self.assertEqual(a.combined, b.combined)

    def test_component_isolation(self):
        base = note_hashes("T", "b", ("x",), "p")
        self.assertEqual(changed_components(note_hashes("U", "b", ("x",), "p"), base), ("title",))
        self.assertEqual(changed_components(note_hashes("T", "c", ("x",), "p"), base), ("body",))
        self.assertEqual(changed_components(note_hashes("T", "b", ("y",), "p"), base), ("tags",))
        self.assertEqual(changed_components(note_hashes("T", "b", ("x",), "q"), base), ("parent",))

    def test_field_boundaries_unambiguous(self):
        # title="ab", body="" must differ from title="a", body="b".
        self.assertNotEqual(
            note_hashes("ab", "", (), "").combined, note_hashes("a", "b", (), "").combined
        )


if __name__ == "__main__":
    unittest.main()
