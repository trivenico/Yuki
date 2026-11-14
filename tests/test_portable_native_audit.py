"""Unit tests for static native loader metadata parsing."""
from __future__ import annotations
import unittest
from scripts.portable_native_audit import (
    parse_elf_dynamic, parse_macho_load_commands, parse_windows_dependents,
)

class NativeAuditTests(unittest.TestCase):
    def test_elf_accepts_origin_relative_runpath(self):
        parsed = parse_elf_dynamic(
            " 0x0000000000000001 (NEEDED) Shared library: [libpython3.so]\n"
            " 0x000000000000001d (RUNPATH) Library runpath: [$ORIGIN/../lib:$ORIGIN]\n"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["needed"], ["libpython3.so"])

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

    def test_macho_rejects_absolute_non_system_dependency(self):
        parsed = parse_macho_load_commands(
            "Load command 1\n cmd LC_RPATH\n path /opt/local/lib (offset 12)\n",
            "binary:\n\t/opt/homebrew/lib/libfoo.dylib (compatibility version 1.0.0)\n",
        )
        self.assertFalse(parsed["ok"])
        self.assertIn("/opt/homebrew/lib/libfoo.dylib", parsed["unsafe_links"])

    def test_windows_parser_reports_imports_without_claiming_resolution(self):
        parsed = parse_windows_dependents(
            "Image has the following dependencies:\n\n KERNEL32.dll\n VCRUNTIME140.dll\n\n"
        )
        self.assertEqual(parsed["dependencies"], ["KERNEL32.dll", "VCRUNTIME140.dll"])
        self.assertFalse(parsed["resolution_verified"])

if __name__ == "__main__":
    unittest.main()
