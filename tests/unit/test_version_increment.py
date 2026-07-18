import unittest

from scripts.check_version_increment import parse_version


class VersionIncrementTest(unittest.TestCase):
    def test_parses_version_as_numeric_tuple(self):
        self.assertEqual(parse_version("1.10.2\n", ".version"), (1, 10, 2))

    def test_numeric_tuple_orders_semver_components(self):
        self.assertGreater(
            parse_version("1.10.0", "current"),
            parse_version("1.9.9", "base"),
        )
        self.assertGreater(
            parse_version("2.0.0", "current"),
            parse_version("1.99.99", "base"),
        )

    def test_rejects_non_release_version(self):
        with self.assertRaisesRegex(ValueError, "plain semver"):
            parse_version("1.2.0-rc1", ".version")


if __name__ == "__main__":
    unittest.main()
