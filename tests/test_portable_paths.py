import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_paths import config_path, data_root, workspace_path


class PortablePathTests(unittest.TestCase):
    def test_default_data_root_is_repository_root(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(data_root(), Path(__file__).resolve().parents[1])

    def test_external_home_receives_relative_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"YUKI_HOME": directory}, clear=True):
                self.assertEqual(data_root(), Path(directory).resolve())
                self.assertEqual(workspace_path("./yuki_workspace"), (Path(directory) / "yuki_workspace").resolve())

    def test_absolute_workspace_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory).resolve()
            with patch.dict(os.environ, {"YUKI_HOME": "/not-used"}, clear=True):
                self.assertEqual(workspace_path(str(absolute)), absolute)

    def test_config_override_can_be_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "portable-config.yaml"
            config.write_text("name: Yuki\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_CONFIG": str(config)}, clear=True):
                self.assertEqual(config_path(), config.resolve())


    @unittest.skipUnless(os.name == "nt", "Windows batch launcher integration test")
    def test_windows_portable_launcher_preserves_and_defaults_yuki_home(self):
        import subprocess

        repository_root = Path(__file__).resolve().parents[1]
        launcher = repository_root / "scripts" / "portable-yuki-windows.bat"
        if (repository_root / ".venv" / "Scripts" / "python.exe").exists():
            self.skipTest("repository virtual environment takes precedence over PATH shim")

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            shim = temp_root / "python.bat"
            capture = temp_root / "captured-home.txt"
            shim.write_text(
                '@echo off\r\n'
                '>>"%YUKI_CAPTURE%" echo %YUKI_HOME%\r\n'
                'exit /b 0\r\n',
                encoding="utf-8",
            )

            for configured, expected in (
                (str(temp_root / "custom-data"), str(temp_root / "custom-data")),
                ("", str(repository_root / "YukiData")),
            ):
                capture.unlink(missing_ok=True)
                env = os.environ.copy()
                env["PATH"] = str(temp_root) + os.pathsep + env.get("PATH", "")
                env["YUKI_CAPTURE"] = str(capture)
                if configured:
                    env["YUKI_HOME"] = configured
                else:
                    env.pop("YUKI_HOME", None)
                completed = subprocess.run(
                    ["cmd.exe", "/d", "/c", "call", str(launcher)],
                    cwd=str(repository_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertTrue(capture.is_file(), completed.stdout + completed.stderr)
                observed = capture.read_text(encoding="utf-8").splitlines()
                self.assertEqual(observed, [expected, expected, expected])


    @unittest.skipUnless(os.name == "posix", "POSIX shell launcher integration test")
    def test_unix_portable_launcher_handles_spaces_and_persists_external_home(self):
        import shutil
        import subprocess

        source_launcher = Path(__file__).resolve().parents[1] / "scripts" / "portable-yuki-unix.sh"
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory) / "Yuki Portable"
            scripts = temp_root / "scripts"
            scripts.mkdir(parents=True)
            launcher = scripts / "portable-yuki-unix.sh"
            shutil.copyfile(source_launcher, launcher)
            launcher.chmod(0o755)
            (temp_root / "main.py").write_text("# integration fixture\\n", encoding="utf-8")

            bin_dir = Path(directory) / "fake bin"
            bin_dir.mkdir()
            python_shim = bin_dir / "python3"
            capture = Path(directory) / "launcher-calls.txt"
            python_shim.write_text(
                "#!/bin/sh\\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\\n"
                "printf '%s|%s|%s\\n' \"$PWD\" \"$YUKI_HOME\" \"$*\" >> \"$YUKI_CAPTURE\"\\n",
                encoding="utf-8",
            )
            python_shim.chmod(0o755)

            data_root = Path(directory) / "External Data"
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["YUKI_HOME"] = str(data_root)
            env["YUKI_CAPTURE"] = str(capture)
            subprocess.run(
                ["sh", str(launcher)],
                cwd=str(Path(directory)),
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                ["sh", str(launcher)],
                cwd=str(Path(directory)),
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            calls = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2)
            for call in calls:
                self.assertEqual(call, f"{temp_root}|{data_root}|main.py")
            self.assertTrue(data_root.is_dir() is False, "launcher should not silently create data storage")


if __name__ == "__main__":
    unittest.main()
