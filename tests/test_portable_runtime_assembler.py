import hashlib
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from scripts.portable_runtime_assembler import assemble_portable_runtime
from scripts.portable_runtime_inputs import InputValidationError


class PortableRuntimeAssemblerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "runtime.zip"
        with zipfile.ZipFile(self.archive, "w") as bundle:
            info = zipfile.ZipInfo("python/bin/python")
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(info, b"pinned interpreter placeholder")
            bundle.writestr("python/stdlib.marker", b"stdlib")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.wheelhouse = self.root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.wheel = self.wheelhouse / "demo-1.0-py3-none-any.whl"
        self.wheel.write_bytes(b"verified dependency wheel bytes")
        app_wheel = self.wheelhouse / "yuki_ai_assistant-0.1.0-py3-none-any.whl"
        app_wheel.write_bytes(b"verified Yuki application wheel bytes")
        wheel_digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        app_digest = hashlib.sha256(app_wheel.read_bytes()).hexdigest()
        self.lock = self.root / "requirements.lock"
        self.lock.write_text(
            f"demo==1.0 --hash=sha256:{wheel_digest}\n"
            f"yuki-ai-assistant==0.1.0 --hash=sha256:{app_digest}\n",
            encoding="utf-8",
        )
        self.runtime = self.root / "assembled-runtime"
        self.dependencies = self.root / "dependencies"

    def _runner(self, returncode=0):
        def run(command, **kwargs):
            staged = Path(command[command.index("--target") + 1])
            staged.mkdir(exist_ok=True)
            (staged / "installed.marker").write_text("ok", encoding="utf-8")
            return SimpleNamespace(returncode=returncode, stderr="simulated pip failure", stdout="")
        return run

    def test_assembles_only_after_archive_digest_verification(self):
        report = assemble_portable_runtime(
            self.archive, self.digest, self.runtime, "python/bin/python",
            self.lock, self.wheelhouse, self.dependencies, runner=self._runner(),
        )
        self.assertEqual(report["runtime_archive"]["sha256"], self.digest)
        self.assertTrue((self.runtime / "python/stdlib.marker").is_file())
        self.assertTrue((self.dependencies / "installed.marker").is_file())
        self.assertFalse(report["network_downloads"])
        self.assertFalse(report["application_executed"])

    def test_assembler_passes_pinned_pip_bootstrap_to_installer(self):
        pip_wheel = self.root / "pip-99.0-py3-none-any.whl"
        with zipfile.ZipFile(pip_wheel, "w") as bundle:
            bundle.writestr("pip/__init__.py", "__version__ = '99.0'\n")
            bundle.writestr("pip/_internal/cli/main.py", "def main(args): return 0\n")
            bundle.writestr("pip-99.0.dist-info/METADATA", "Name: pip\nVersion: 99.0\n")
        pip_digest = hashlib.sha256(pip_wheel.read_bytes()).hexdigest()
        captured = {}
        def runner(command, **kwargs):
            captured["command"] = command
            staged = Path(command[command.index("--target") + 1])
            staged.mkdir(exist_ok=True)
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        report = assemble_portable_runtime(
            self.archive, self.digest, self.runtime, "python/bin/python",
            self.lock, self.wheelhouse, self.dependencies, runner=runner,
            pip_bootstrap_wheel=pip_wheel, pip_bootstrap_sha256=pip_digest,
            pip_bootstrap_version="99.0",
        )
        self.assertIn("-c", captured["command"])
        snapshot_arg = captured["command"][captured["command"].index("-c") + 2]
        self.assertTrue(Path(snapshot_arg).name == pip_wheel.name)
        self.assertIn(".yuki-pip-wheel-", str(Path(snapshot_arg).parent))
        self.assertNotEqual(Path(snapshot_arg), pip_wheel)
        self.assertFalse(Path(snapshot_arg).exists())
        self.assertEqual(report["dependencies"]["pip_bootstrap"], "pinned-wheel")

    def test_requires_yuki_application_wheel_pin(self):
        self.lock.write_text("demo==1.0 --hash=sha256:" + hashlib.sha256(self.wheel.read_bytes()).hexdigest() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(InputValidationError, "must include an exact yuki-ai-assistant"):
            assemble_portable_runtime(
                self.archive, self.digest, self.runtime, "python/bin/python",
                self.lock, self.wheelhouse, self.dependencies, runner=self._runner(),
            )
        self.assertFalse(self.runtime.exists())

    def test_rejects_wrong_archive_digest_without_extracting(self):
        with self.assertRaisesRegex(InputValidationError, "does not match"):
            assemble_portable_runtime(
                self.archive, "0" * 64, self.runtime, "python/bin/python",
                self.lock, self.wheelhouse, self.dependencies, runner=self._runner(),
            )
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.dependencies.exists())

    def test_rejects_traversal_in_runtime_interpreter_path(self):
        with self.assertRaisesRegex(InputValidationError, "must not be absolute or traverse"):
            assemble_portable_runtime(
                self.archive, self.digest, self.runtime, "../host-python",
                self.lock, self.wheelhouse, self.dependencies, runner=self._runner(),
            )
        self.assertFalse(self.runtime.exists())

    def test_failed_dependency_install_removes_extracted_runtime(self):
        with self.assertRaisesRegex(InputValidationError, "installation failed"):
            assemble_portable_runtime(
                self.archive, self.digest, self.runtime, "python/bin/python",
                self.lock, self.wheelhouse, self.dependencies,
                runner=self._runner(returncode=1),
            )
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.dependencies.exists())

    def test_destinations_must_be_new(self):
        self.runtime.mkdir()
        with self.assertRaisesRegex(InputValidationError, "Runtime destination"):
            assemble_portable_runtime(
                self.archive, self.digest, self.runtime, "python/bin/python",
                self.lock, self.wheelhouse, self.dependencies, runner=self._runner(),
            )

    def test_destinations_must_share_parent(self):
        other = self.root / "other"
        other.mkdir()
        with self.assertRaisesRegex(InputValidationError, "share the same parent"):
            assemble_portable_runtime(
                self.archive, self.digest, self.runtime, "python/bin/python",
                self.lock, self.wheelhouse, other / "dependencies", runner=self._runner(),
            )


if __name__ == "__main__":
    unittest.main()
