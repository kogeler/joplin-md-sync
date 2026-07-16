import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync.paths import (
    folder_dirname,
    note_filename,
    safe_rel_path,
    sanitize_component,
)

NID = "17a35454fbb34ee080e29fba9ee88730"


class SanitizeTest(unittest.TestCase):
    def test_windows_reserved_names(self):
        for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"):
            out = sanitize_component(name)
            self.assertNotEqual(out.split(".")[0].upper(), name.upper(), out)
        self.assertEqual(sanitize_component("CON"), "_CON")
        # Reserved even with an extension-like suffix.
        self.assertEqual(sanitize_component("con.txt"), "_con.txt")

    def test_invalid_windows_chars_replaced(self):
        out = sanitize_component('a<b>c:d"e/f\\g|h?i*j')
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, out)

    def test_control_chars_removed(self):
        self.assertNotIn("\x07", sanitize_component("a\x07b"))

    def test_trailing_dots_and_spaces(self):
        self.assertEqual(sanitize_component("name. .."), "name")
        self.assertEqual(sanitize_component("name   "), "name")

    def test_empty_title(self):
        self.assertEqual(sanitize_component(""), "untitled")
        self.assertEqual(sanitize_component("???"), "___")

    def test_dots_only_is_not_traversal(self):
        self.assertEqual(sanitize_component(".."), "untitled")
        self.assertEqual(sanitize_component("."), "untitled")

    def test_unicode_preserved(self):
        self.assertEqual(sanitize_component("Заметки по k8s"), "Заметки по k8s")

    def test_long_titles_truncated(self):
        self.assertLessEqual(len(sanitize_component("x" * 500)), 80)


class FilenameTest(unittest.TestCase):
    def test_note_filename_format(self):
        self.assertEqual(note_filename("Kubernetes", NID), "Kubernetes--17a35454.md")

    def test_case_insensitive_folder_collision(self):
        taken = {"work"}
        self.assertEqual(folder_dirname("Work", NID, taken), f"Work--{NID[:8]}")
        self.assertEqual(folder_dirname("Other", NID, taken), "Other")


class SafeRelPathTest(unittest.TestCase):
    def test_traversal_rejected(self):
        for bad in ("../x.md", "a/../../x.md", "/abs/x.md", "C:evil.md", "c:\\evil.md", ""):
            self.assertFalse(safe_rel_path(bad), bad)

    def test_normal_paths_accepted(self):
        for good in ("Work/a.md", "Work/Sub/b.md", "a.md"):
            self.assertTrue(safe_rel_path(good), good)


if __name__ == "__main__":
    unittest.main()
