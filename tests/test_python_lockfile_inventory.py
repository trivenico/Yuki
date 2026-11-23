import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.python_lockfile_inventory import (
    parse_poetry_lock,
    parse_pyproject_metadata,
    parse_python_lockfile,
    parse_requirements_lock,
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

    def test_rejects_uv_path_editable_and_workspace_sources(self):
        for source in (
            '{ path = "../local-package" }',
            '{ virtual = true }',
        ):
            with self.subTest(source=source):
                with self.assertRaises(RuntimeInventoryError):
                    parse_uv_lock(
                        'version = 1\n'
                        '[[package]]\nname = "demo"\nversion = "1.0"\n'
                        'source = ' + source + '\n'
                    )


    def test_parses_exact_requirements_and_normalizes_names(self):
        self.assertEqual(
            parse_requirements_lock(
                "# generated lock\nRequests[socks]==2.32.3\n"
                "typing_extensions == 4.12.2\n"
            ),
            {"requests": "2.32.3", "typing-extensions": "4.12.2"},
        )

    def test_rejects_unpinned_marked_included_and_duplicate_requirements(self):
        invalid = (
            "requests>=2.0\n",
            "requests==2.0; python_version >= '3.10'\n",
            "-r base.txt\n",
            "-e ../local\n",
            "requests @ https://example.invalid/requests.whl\n",
            "Demo_Pkg==1.0\ndemo-pkg==1.0\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(RuntimeInventoryError):
                    parse_requirements_lock(raw)


    def test_excludes_uv_editable_workspace_root(self):
        self.assertEqual(
            parse_uv_lock(
                'version = 1\n'
                '[[package]]\nname = "yuki"\nversion = "0.1.0"\n'
                'source = { editable = "." }\n'
                '[[package]]\nname = "requests"\nversion = "2.32.3"\n'
            ),
            {"requests": "2.32.3"},
        )


    def test_rejects_malformed_lock_version_and_source_types(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_poetry_lock(
                'metadata = { lock-version = ["2.0"] }\\n'
                '[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            )
        with self.assertRaises(RuntimeInventoryError):
            parse_poetry_lock(
                'metadata = { lock-version = "2.0" }\\n'
                '[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
                'source = { type = ["pypi"] }\\n'
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


    def test_accepts_only_canonical_poetry_and_uv_registry_sources(self):
        poetry = (
            'metadata = { lock-version = "2.0" }\\n'
            '[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { type = "legacy", url = "https://pypi.org/simple", reference = "pypi" }\\n'
        )
        self.assertEqual(parse_poetry_lock(poetry), {"demo": "1.0"})
        uv = (
            'version = 1\\n[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { registry = "https://pypi.org/simple/" }\\n'
        )
        self.assertEqual(parse_uv_lock(uv), {"demo": "1.0"})

    def test_rejects_custom_registries_and_malformed_source_fields(self):
        invalid = (
            'version = 1\\n[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { registry = "https://packages.example.invalid/simple" }\\n',
            'version = 1\\n[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { registry = ["https://pypi.org/simple"] }\\n',
            'version = 1\\n[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { registry = "https://pypi.org/simple", git = "https://example.invalid/demo" }\\n',
            'metadata = { lock-version = "2.0" }\\n'
            '[[package]]\\nname = "demo"\\nversion = "1.0"\\n'
            'source = { type = "legacy", url = "https://packages.example.invalid/simple" }\\n',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                parser = parse_poetry_lock if raw.startswith("metadata") else parse_uv_lock
                with self.assertRaises(RuntimeInventoryError):
                    parser(raw)


    def test_parses_pep621_project_and_optional_dependency_roles(self):
        result = parse_pyproject_metadata(
            '[project]\\nname = "Yuki-Agent"\\nversion = "1.2.3"\\n'
            'dependencies = ["requests>=2.0", "typing_extensions==4.12.2"]\\n'
            '[project.optional-dependencies]\\n'
            'desktop = ["textual>=0.70", "requests>=2.0"]\\n'
            'dev = ["pytest>=8"]\\n'
        )
        self.assertEqual(result["name"], "yuki-agent")
        self.assertEqual(result["version"], "1.2.3")
        self.assertEqual(result["dependency_roles"]["requests"], ("optional:desktop", "runtime"))
        self.assertEqual(result["dependency_roles"]["pytest"], ("optional:dev",))

    def test_parses_pep508_extras_and_environment_markers(self):
        result = parse_pyproject_metadata(
            '[project]\nname = "demo"\ndependencies = '
            '["requests[security]>=2.0; python_version >= "3.10""]\n'
            '["requests[security]>=2.0; python_version >= \\"3.10\\""]\n'
        self.assertEqual(result["dependency_roles"]["requests"], ("runtime",))

    def test_rejects_dynamic_project_name_and_untrusted_dependency_urls(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_pyproject_metadata('[project]\\ndynamic = ["name"]\\n')
        with self.assertRaises(RuntimeInventoryError):
            parse_pyproject_metadata(
                '[project]\\nname = "demo"\\ndependencies = '
                '["demo @ https://example.invalid/demo.whl"]\\n'
            )

    def test_rejects_malformed_optional_dependency_groups(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_pyproject_metadata(
                '[project]\\nname = "demo"\\n'
                '[project.optional-dependencies]\\n"bad group" = ["requests>=2"]\\n'
            )


if __name__ == "__main__":
    unittest.main()
