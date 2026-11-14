import hashlib
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from scripts.portable_runtime_inputs import (
    InputValidationError,
    validate_offline_wheelhouse,
    validate_runtime_archive,
    install_offline_dependencies,
)


class PortableRuntimeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "python-runtime.tar.gz"
        self.archive.write_bytes(b"pinned runtime archive")
        self.wheelhouse = self.root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.wheel = self.wheelhouse / "demo-1.0-py3-none-any.whl"
        self.wheel.write_bytes(b"wheel bytes")
        self.lock = self.root / "requirements.lock"
        self.digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()

    def _write_lock(self, text=None):
        if text is None:
            text = f"demo==1.0 --hash=sha256:{self.digest}\n"
        self.lock.write_text(text, encoding="utf-8")

    def test_runtime_archive_requires_exact_sha256(self):
        expected = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        report = validate_runtime_archive(self.archive, expected)
        self.assertEqual(report["sha256"], expected)
        with self.assertRaisesRegex(InputValidationError, "does not match"):
            validate_runtime_archive(self.archive, "0" * 64)

    def test_offline_wheelhouse_accepts_fully_hashed_wheel(self):
        self._write_lock()
        report = validate_offline_wheelhouse(self.lock, self.wheelhouse)
        self.assertEqual(report["requirement_count"], 1)
        self.assertEqual(report["wheel_count"], 1)

    def test_lock_requires_exact_version_pin(self):
        self._write_lock(f"demo>=1.0 --hash=sha256:{self.digest}\n")
        with self.assertRaisesRegex(InputValidationError, "exact == version pin"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_lock_requires_hashes(self):
        self._write_lock("demo==1.0\n")
        with self.assertRaisesRegex(InputValidationError, "SHA-256"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_unreferenced_wheel_is_rejected(self):
        self._write_lock(f"demo==1.0 --hash=sha256:{self.digest}\n")
        (self.wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
        with self.assertRaisesRegex(InputValidationError, "absent from the lock"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_source_archive_is_rejected(self):
        self._write_lock()
        (self.wheelhouse / "demo.tar.gz").write_bytes(b"sdist")
        with self.assertRaisesRegex(InputValidationError, "wheels only"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_pip_options_are_rejected(self):
        self._write_lock("--extra-index-url https://example.invalid/simple\n")
        with self.assertRaisesRegex(InputValidationError, "Standalone pip options"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_lock_rejects_unrecognized_requirement_options(self):
        self._write_lock(f"demo==1.0 --hash=sha256:{self.digest} --global-option=evil\n")
        with self.assertRaisesRegex(InputValidationError, "only SHA-256 hash options"):
            validate_offline_wheelhouse(self.lock, self.wheelhouse)

    def test_installer_uses_isolated_offline_hash_required_pip(self):
        self._write_lock()
        python = self.root / "python"
        python.write_bytes(b"trusted interpreter placeholder")
        target = self.root / "site-packages"
        calls = []
        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            target.mkdir()
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        result = install_offline_dependencies(python, self.lock, self.wheelhouse, target, runner=fake_runner)
        command, kwargs = calls[0]
        self.assertIn("--no-index", command)
        self.assertIn("--require-hashes", command)
        self.assertIn("--isolated", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertFalse(kwargs["shell"])
        self.assertNotIn("PIP_INDEX_URL", kwargs["env"])
        self.assertTrue(result["hash_checking"])

    def test_installer_rejects_failed_pip_execution(self):
        self._write_lock()
        python = self.root / "python"
        python.write_bytes(b"trusted interpreter placeholder")
        def failed_runner(command, **kwargs):
            return SimpleNamespace(returncode=1, stderr="bad wheel", stdout="")
        with self.assertRaisesRegex(InputValidationError, "bad wheel"):
            install_offline_dependencies(python, self.lock, self.wheelhouse, self.root / "site", runner=failed_runner)


if __name__ == "__main__":
    unittest.main()
