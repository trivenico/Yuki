"""Regression checks for the portable launcher contracts."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PortableLauncherTests(unittest.TestCase):
    def test_windows_launcher_preserves_explicit_data_root(self):
        script = (ROOT / "scripts" / "portable-yuki-windows.bat").read_text(encoding="utf-8")
        self.assertIn('if not defined YUKI_HOME set "YUKI_HOME=', script)
        self.assertIn("sys.version_info >= (3,10)", script)
        self.assertIn("import yaml", script)

    def test_unix_launcher_preserves_explicit_data_root_and_checks_python(self):
        script = (ROOT / "scripts" / "portable-yuki-unix.sh").read_text(encoding="utf-8")
        self.assertIn('export YUKI_HOME="${YUKI_HOME:-$YUKI_ROOT/YukiData}"', script)
        self.assertIn("sys.version_info >= (3,10)", script)
        self.assertIn("import yaml", script)


if __name__ == "__main__":
    unittest.main()
