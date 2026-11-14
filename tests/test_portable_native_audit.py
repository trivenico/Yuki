"""Unit tests for static native loader metadata parsing."""
from __future__ import annotations
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.portable_native_audit import (
    audit_bundle, parse_elf_dynamic, parse_macho_load_commands, parse_windows_dependents,
)

class NativeAuditTests(unittest.TestCase):
    def test_elf_accepts_origin_relative_runpath(self):
        parsed = parse_elf_dynamic(
            " 0x0000000000000001 (NEEDED) Shared library: [libpython3.so]\n"
            " 0x000000000000001d (RUNPATH) Library runpath: [$ORIGIN/../lib:$ORIGIN]\n"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["needed"], ["libpython3.so"])

    def test_elf_rejects_absolute_needed_path(self):
        parsed = parse_elf_dynamic(" 0x0000000000000001 (NEEDED) Shared library: [/opt/vendor/libfoo.so]\n")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["unsafe_needed_paths"], ["/opt/vendor/libfoo.so"])

    def test_elf_rejects_absolute_or_non_origin_runpath(self):
        parsed = parse_elf_dynamic(
            " 0x000000000000001d (RPATH) Library rpath: [/usr/local/lib:$ORIGIN/lib]\n"
        )
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["unsafe_loader_paths"], ["/usr/local/lib"])

    def test_macho_accepts_relative_loader_paths_and_system_libraries(self):
        loads = "Load command 1\n          cmd LC_RPATH\n      cmdsize 40\n         path @loader_path/../Frameworks (offset 12)\n"
        links = "binary:\n\t@rpath/libYuki.dylib (compatibility version 1.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
        parsed = parse_macho_load_commands(loads, links)
        self.assertTrue(parsed["ok"])

    def test_macho_rejects_unqualified_non_system_install_name(self):
        parsed = parse_macho_load_commands("binary has no custom rpath\n", "binary:\n\tlibvendor.dylib (compatibility version 1.0.0)\n")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["unsafe_links"], ["libvendor.dylib"])

    def test_macho_rejects_absolute_non_system_dependency(self):
        parsed = parse_macho_load_commands(
            "Load command 1\n cmd LC_RPATH\n path /opt/local/lib (offset 12)\n",
            "binary:\n\t/opt/homebrew/lib/libfoo.dylib (compatibility version 1.0.0)\n",
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("/opt/homebrew/lib/libfoo.dylib", parsed["unsafe_links"])

    def test_macho_rejects_loader_path_prefix_lookalikes(self):
        for token in ("@loader_pathEvil", "@executable_pathEvil"):
            with self.subTest(token=token):
                loads = f"Load command 1\n cmd LC_RPATH\n path {token}/lib (offset 12)\n"
                parsed = parse_macho_load_commands(loads, "binary:\n\t@rpath/libYuki.dylib (compatibility version 1.0.0)\n")
                self.assertFalse(parsed["ok"])
                self.assertIn(token + "/lib", parsed["unsafe_rpaths"])

    def test_macho_accepts_exact_loader_tokens(self):
        for token in ("@loader_path", "@executable_path"):
            with self.subTest(token=token):
                loads = f"Load command 1\n cmd LC_RPATH\n path {token} (offset 12)\n"
                parsed = parse_macho_load_commands(loads, "binary:\n\t@rpath/libYuki.dylib (compatibility version 1.0.0)\n")
                self.assertTrue(parsed["ok"])

    def test_macho_universal_architecture_headers_are_not_install_names(self):
        loads = "binary has no custom rpath\n"
        links = (
            "Universal binary with 2 architectures: [x86_64:arm64]\n"
            "/bundle/libYuki.dylib (architecture x86_64):\n"
            "\t@rpath/libvendor.dylib (compatibility version 1.0.0)\n"
            "/bundle/libYuki.dylib (architecture arm64):\n"
            "\t@rpath/libvendor.dylib (compatibility version 1.0.0)\n"
        )
        parsed = parse_macho_load_commands(loads, links)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["install_names"], ["@rpath/libvendor.dylib", "@rpath/libvendor.dylib"])

    def test_macos_audit_inspects_all_universal_binary_architectures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "universal.dylib"
            binary.write_bytes(bytes((0xCA, 0xFE, 0xBA, 0xBF)))
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                if "-l" in command:
                    output = "Load command 1\n cmd LC_RPATH\n path @loader_path (offset 12)\n"
                else:
                    output = (
                        "Universal binary with 2 architectures: [x86_64:arm64]\n"
                        "universal.dylib (architecture x86_64):\n"
                        "\t@rpath/libvendor.dylib (compatibility version 1.0.0)\n"
                        "universal.dylib (architecture arm64):\n"
                        "\t@rpath/libvendor.dylib (compatibility version 1.0.0)\n"
                    )
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

            with patch("scripts.portable_native_audit.shutil.which", return_value="/usr/bin/otool"):
                report = audit_bundle(root, "macos", runner=runner)

            self.assertEqual(report["binary_count"], 1)
            self.assertTrue(all(command[1:3] == ["-arch", "all"] for command in commands))
            self.assertEqual(len(commands), 2)

    def test_windows_parser_reports_imports_without_claiming_resolution(self):
        parsed = parse_windows_dependents(
            "Image has the following dependencies:\n\n KERNEL32.dll\n VCRUNTIME140.dll\n\n"
        )
        self.assertEqual(parsed["dependencies"], ["KERNEL32.dll", "VCRUNTIME140.dll"])
        self.assertFalse(parsed["resolution_verified"])

if __name__ == "__main__":
    unittest.main()
