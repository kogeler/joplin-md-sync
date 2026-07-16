import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.metadata import (
    MetadataError,
    emit_note_file,
    has_header,
    parse_note_file,
    serialize_header,
)

NID = "17a35454fbb34ee080e29fba9ee88730"


class HeaderTest(unittest.TestCase):
    def test_round_trip(self):
        text = emit_note_file(NID, "Kubernetes", ("b", "a"), "# Body\n\ntext\n")
        parsed = parse_note_file(text)
        self.assertEqual(parsed.note_id, NID)
        self.assertEqual(parsed.title, "Kubernetes")
        self.assertEqual(parsed.tags, ("a", "b"))
        self.assertEqual(parsed.body, "# Body\n\ntext\n")

    def test_header_is_single_first_line_with_sorted_keys(self):
        header = serialize_header(NID, "T", ("z", "a"))
        self.assertNotIn("\n", header)
        self.assertTrue(header.startswith("<!-- joplin-md-sync: {"))
        payload = header[len("<!-- joplin-md-sync: ") : -len(" -->")]
        self.assertEqual(
            payload, f'{{"id":"{NID}","schema":1,"tags":["a","z"],"title":"T"}}'
        )

    def test_cyrillic_title_preserved(self):
        text = emit_note_file(NID, "Заметка про кластер", (), "тело\n")
        parsed = parse_note_file(text)
        self.assertEqual(parsed.title, "Заметка про кластер")
        self.assertIn("Заметка", text)  # ensure_ascii=False

    def test_comment_terminator_in_title_escaped(self):
        title = "evil --> breaker"
        text = emit_note_file(NID, title, (), "body")
        first_line = text.split("\n", 1)[0]
        self.assertTrue(first_line.endswith(" -->"))
        self.assertEqual(first_line.count("-->"), 1)
        self.assertEqual(parse_note_file(text).title, title)

    def test_missing_id_means_new_note(self):
        parsed = parse_note_file(emit_note_file(None, "New", (), "b"))
        self.assertIsNone(parsed.note_id)

    def test_empty_body(self):
        parsed = parse_note_file(serialize_header(NID, "T", ()) + "\n")
        self.assertEqual(parsed.body, "")

    def test_crlf_input_canonicalized(self):
        text = serialize_header(NID, "T", ()) + "\r\n\r\nline1\r\nline2"
        parsed = parse_note_file(text)
        self.assertEqual(parsed.body, "line1\nline2")


class MalformedHeaderTest(unittest.TestCase):
    def assert_invalid(self, text: str, fragment: str):
        with self.assertRaises(MetadataError) as ctx:
            parse_note_file(text)
        self.assertIn(fragment, str(ctx.exception))

    def test_no_header(self):
        self.assert_invalid("plain markdown\n", "missing")
        self.assertFalse(has_header("plain markdown\n"))

    def test_bad_json(self):
        self.assert_invalid("<!-- joplin-md-sync: {not json} -->\n\nbody", "not valid JSON")

    def test_wrong_schema(self):
        self.assert_invalid(
            '<!-- joplin-md-sync: {"schema":99,"tags":[],"title":"t"} -->\n\nb', "schema"
        )

    def test_bad_id(self):
        self.assert_invalid(
            '<!-- joplin-md-sync: {"id":"XYZ","schema":1,"tags":[],"title":"t"} -->\n\nb',
            "32-character",
        )

    def test_unknown_keys(self):
        self.assert_invalid(
            '<!-- joplin-md-sync: {"schema":1,"tags":[],"title":"t","updated_time":5} -->\n\nb',
            "unknown metadata keys",
        )

    def test_missing_blank_separator(self):
        self.assert_invalid(
            '<!-- joplin-md-sync: {"schema":1,"tags":[],"title":"t"} -->\nbody-without-blank',
            "blank line",
        )

    def test_multiline_comment_rejected(self):
        self.assert_invalid("<!-- joplin-md-sync: {\n} -->\n\nb", "single-line")


if __name__ == "__main__":
    unittest.main()
