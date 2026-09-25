import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_repo_data_size import human_readable, oversized_files


class HumanReadableTest(unittest.TestCase):
    def test_formats_bytes_to_kb_and_mb(self) -> None:
        self.assertEqual(human_readable(512), "512.0B")
        self.assertEqual(human_readable(2048), "2.0KB")
        self.assertEqual(human_readable(3 * 1024 * 1024), "3.0MB")


class OversizedFilesTest(unittest.TestCase):
    def test_flags_files_above_the_limit_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        big_file = root / "data" / "takeout_customer_service_seed.jsonl"
        small_file = root / "pytest.ini"

        with patch(
            "scripts.check_repo_data_size.tracked_files",
            return_value=[big_file, small_file],
        ):
            offenders = oversized_files(root, max_bytes=1024)

        offenders_paths = [path for path, _ in offenders]
        self.assertIn(big_file, offenders_paths)
        self.assertNotIn(small_file, offenders_paths)

    def test_returns_empty_when_everything_fits(self) -> None:
        root = Path(__file__).resolve().parents[1]

        with patch(
            "scripts.check_repo_data_size.tracked_files",
            return_value=[root / "pytest.ini"],
        ):
            offenders = oversized_files(root, max_bytes=10 * 1024 * 1024)

        self.assertEqual(offenders, [])

    def test_current_repo_passes_the_one_mb_default(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertEqual(oversized_files(root, max_bytes=1024 * 1024), [])

    def test_skips_paths_that_no_longer_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]

        with patch(
            "scripts.check_repo_data_size.tracked_files",
            return_value=[root / "does-not-exist.jsonl"],
        ):
            offenders = oversized_files(root, max_bytes=1)

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
