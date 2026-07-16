import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from joplin_md_sync import update_check
from joplin_md_sync.errors import ApiError


class ParseVersionTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(update_check.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(update_check.parse_version("1.2.3"), (1, 2, 3))

    def test_invalid(self):
        for bad in ("v1.2", "1.2.3-rc1", "latest", ""):
            self.assertIsNone(update_check.parse_version(bad), bad)


class CheckForUpdateTest(unittest.TestCase):
    def test_outdated_detected(self):
        with mock.patch.object(
            update_check, "_fetch_json", return_value={"tag_name": "v99.0.0"}
        ):
            result = update_check.check_for_update()
        self.assertTrue(result["outdated"])
        self.assertIn("v99.0.0", result["update_command"])

    def test_current_version_ok(self):
        with mock.patch.object(
            update_check, "_fetch_json", return_value={"tag_name": "v1.0.0"}
        ):
            result = update_check.check_for_update()
        self.assertFalse(result["outdated"])

    def test_prereleases_excluded_by_default_uses_latest_endpoint(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return {"tag_name": "v1.0.0"}

        with mock.patch.object(update_check, "_fetch_json", side_effect=fake_fetch):
            update_check.check_for_update()
        self.assertTrue(calls[0].endswith("/releases/latest"))

    def test_network_failure_is_operational_error(self):
        with mock.patch.object(update_check, "_fetch_json", side_effect=OSError("boom")):
            with self.assertRaises(ApiError) as ctx:
                update_check.check_for_update()
        self.assertEqual(ctx.exception.code, "UPDATE_CHECK_FAILED")

    def test_no_stable_release(self):
        with mock.patch.object(
            update_check, "_fetch_json", return_value={"tag_name": "nightly"}
        ):
            with self.assertRaises(ApiError) as ctx:
                update_check.check_for_update()
        self.assertEqual(ctx.exception.code, "UPDATE_CHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
