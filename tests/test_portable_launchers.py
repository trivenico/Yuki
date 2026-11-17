import unittest
from pathlib import Path


class PortableLauncherTests(unittest.TestCase):
    def test_windows_launcher_preserves_explicit_data_root_and_quotes_python(self):
        launcher = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "portable-yuki-windows.bat"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'if not defined YUKI_HOME set "YUKI_HOME=%YUKI_ROOT%\\YukiData"',
            launcher,
        )
        self.assertNotIn('set "YUKI_HOME=%YUKI_ROOT%\\YukiData"\\ncd', launcher)
        self.assertIn('"%%YUKI_PYTHON%%" --version', launcher.replace("%YUKI_PYTHON%", "%%YUKI_PYTHON%%"))
        self.assertIn('"%%YUKI_PYTHON%%" -c "import yaml"', launcher.replace("%YUKI_PYTHON%", "%%YUKI_PYTHON%%"))
        self.assertIn('"%%YUKI_PYTHON%%" main.py', launcher.replace("%YUKI_PYTHON%", "%%YUKI_PYTHON%%"))


if __name__ == "__main__":
    unittest.main()
