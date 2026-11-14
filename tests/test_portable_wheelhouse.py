import hashlib
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.portable_runtime_inputs import InputValidationError
from scripts.portable_wheelhouse import build_wheelhouse


class PortableWheelhouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wheelhouse = self.root / "wheelhouse"
        self.wheel = self.root / "yuki_ai_assistant-1.0-py3-none-any.whl"
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("yuki_ai_assistant/__init__.py", "")
            archive.writestr("yuki_ai_assistant-1.0.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: yuki-ai-assistant\nVersion: 1.0\n")
        digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.lock = self.root / "requirements.lock"
        self.lock.write_text(
            f"yuki-ai-assistant==1.0 --hash=sha256:{digest}\n", encoding="utf-8"
        )

    def _runner(self, command, **kwargs):
        self.assertFalse(kwargs["shell"])
        self.assertIn("--require-hashes", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertIn("--no-deps", command)
        self.assertIn("--platform", command)
        target = Path(command[command.index("--dest") + 1])
        (target / self.wheel.name).write_bytes(self.wheel.read_bytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_builds_and_atomically_publishes_valid_wheelhouse(self):
        report = build_wheelhouse(
            self.lock, self.wheelhouse, platform="manylinux_2_28_x86_64",
            python_version="3.12", implementation="cp", abi="cp312",
            runner=self._runner,
        )
        self.assertEqual(report["wheel_count"], 1)
        self.assertTrue(report["atomic_publish"])
        self.assertTrue((self.wheelhouse / self.wheel.name).is_file())

    def test_failed_download_does_not_publish_destination(self):
        def fail(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "download failed")
        with self.assertRaisesRegex(InputValidationError, "download failed"):
            build_wheelhouse(
                self.lock, self.wheelhouse, platform="manylinux_2_28_x86_64",
                python_version="3.12", implementation="cp", abi="cp312",
                runner=fail,
            )
        self.assertFalse(self.wheelhouse.exists())
        self.assertEqual(list(self.root.glob(".yuki-wheelhouse-stage-*")), [])

    def test_rejects_unpinned_lock_before_download(self):
        self.lock.write_text("yuki-ai-assistant>=1.0\n", encoding="utf-8")
        called = []
        with self.assertRaises(InputValidationError):
            build_wheelhouse(
                self.lock, self.wheelhouse, platform="manylinux_2_28_x86_64",
                python_version="3.12", implementation="cp", abi="cp312",
                runner=lambda *a, **k: called.append(True),
            )
        self.assertEqual(called, [])

    def test_rejects_insecure_index_url(self):
        with self.assertRaisesRegex(InputValidationError, "HTTPS"):
            build_wheelhouse(
                self.lock, self.wheelhouse, platform="manylinux_2_28_x86_64",
                python_version="3.12", implementation="cp", abi="cp312",
                index_url="http://packages.example.com/simple",
                runner=self._runner,
            )

    def test_rejects_existing_destination(self):
        self.wheelhouse.mkdir()
        with self.assertRaisesRegex(InputValidationError, "must not already exist"):
            build_wheelhouse(
                self.lock, self.wheelhouse, platform="manylinux_2_28_x86_64",
                python_version="3.12", implementation="cp", abi="cp312",
                runner=self._runner,
            )


if __name__ == "__main__":
    unittest.main()
