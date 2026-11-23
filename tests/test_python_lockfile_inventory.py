import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.python_lockfile_inventory import (
    parse_poetry_lock,
    parse_python_lockfile,
    parse_uv_lock,
)


class PythonLockfileInventoryTests(unittest.TestCase):
    def test_parses_poetry_lock_packages(self):
        raw = '''
metadata = { lock-version = "2.0" }
[[package]]
name = "PyYAML"
version = "6.0.3"

[[package]]
name = "typing_extensions"
version = "4.12.2"
'''
        self.assertEqual(parse_poetry_lock(raw), {
            "pyyaml": "6.0.3",
            "typing-extensions": "4.12.2",
        })

    def test_parses_uv_lock_packages(self):
        raw = '''
version = 1
revision = 3
requires-python = ">=3.10"

[[package]]
name = "requests"
version = "2.32.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "urllib3"
version = "2.2.3"
'''
        self.assertEqual(parse_uv_lock(raw), {
            "requests": "2.32.3",
            "urllib3": "2.2.3",
        })

    def test_dispatches_only_known_formats(self):
        self.assertEqual(
            parse_python_lockfile(
                'version = 1\n[[package]]\nname = "demo"\nversion = "1.0.0"\n',
                "uv",
            ),
            {"demo": "1.0.0"},
        )
        with self.assertRaises(RuntimeInventoryError):
            parse_python_lockfile("", "pipenv")

    def test_rejects_duplicate_toml_keys_and_duplicate_normalized_names(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock('version = 1\nversion = 1\n')
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(
                'version = 1\n'
                '[[package]]\nname = "Demo_Pkg"\nversion = "1.0"\n'
                '[[package]]\nname = "demo-pkg"\nversion = "1.0"\n'
            )

    def test_rejects_conflicting_versions(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(
                'version = 1\n'
                '[[package]]\nname = "demo"\nversion = "1.0"\n'
                '[[package]]\nname = "demo"\nversion = "2.0"\n'
            )

    def test_rejects_git_and_editable_sources(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(
                'version = 1\n'
                '[[package]]\nname = "demo"\nversion = "1.0"\n'
                'source = { git = "https://example.invalid/demo.git" }\n'
            )
        with self.assertRaises(RuntimeInventoryError):
            parse_poetry_lock(
                'metadata = { lock-version = "2.0" }\n'
                '[[package]]\nname = "demo"\nversion = "1.0"\n'
                'develop = true\n'
            )

    def test_rejects_invalid_versions_and_unsupported_lock_versions(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock('version = 9\n')
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(
                'version = 1\n[[package]]\nname = "demo"\nversion = "*"\n'
            )

    def test_rejects_oversized_and_invalid_utf8_input(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(" " * (4 * 1024 * 1024 + 1))
        with self.assertRaises(RuntimeInventoryError):
            parse_uv_lock(b"\xff")


if __name__ == "__main__":
    unittest.main()
