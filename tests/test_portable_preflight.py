import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.portable_preflight import PortablePreflightError, validate_portable_runtime


class PortablePreflightTests(unittest.TestCase):
    def test_rejects_python_older_than_310(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PortablePreflightError, "Python 3.10"):
                validate_portable_runtime(version_info=(3, 9, 18), data_home=directory)

    def test_requires_explicit_data_home(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(PortablePreflightError, "YUKI_HOME"):
                validate_portable_runtime()

    def test_creates_and_checks_writable_external_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory) / "Yuki Data"
            resolved = validate_portable_runtime(version_info=(3, 11, 9), data_home=data_home)
            self.assertEqual(resolved, data_home.resolve())
            self.assertTrue(data_home.is_dir())
            self.assertEqual(list(data_home.iterdir()), [])

    def test_rejects_symlink_data_home(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            link = base / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with self.assertRaisesRegex(PortablePreflightError, "symbolic link"):
                validate_portable_runtime(data_home=link)

    def test_rejects_file_instead_of_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "not-a-directory"
            file_path.write_text("x", encoding="utf-8")
            with self.assertRaises(PortablePreflightError):
                validate_portable_runtime(data_home=file_path)

    def test_reports_unwritable_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.portable_preflight.tempfile.NamedTemporaryFile", side_effect=PermissionError("read only")):
                with self.assertRaisesRegex(PortablePreflightError, "not writable"):
                    validate_portable_runtime(data_home=directory)


if __name__ == "__main__":
    unittest.main()
