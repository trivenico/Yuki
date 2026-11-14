import base64
import csv
import hashlib
import importlib.util
import io
import tempfile
import sys
import subprocess
import zipfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from pathlib import Path

from scripts.portable_runtime_inputs import (
    InputValidationError,
    validate_offline_wheelhouse,
    validate_runtime_archive,
    install_offline_dependencies,
    main,
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
        runtime_root = self.root / "verified-runtime"
        runtime_root.mkdir()
        python = runtime_root / "python"
        python.write_bytes(b"trusted interpreter placeholder")
        target = self.root / "site-packages"
        calls = []
        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            staged_target = Path(command[command.index("--target") + 1])
            staged_target.mkdir(exist_ok=True)
            (staged_target / "installed.marker").write_text("ok", encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        result = install_offline_dependencies(runtime_root, python, self.lock, self.wheelhouse, target, runner=fake_runner)
        command, kwargs = calls[0]
        self.assertIn("--no-index", command)
        self.assertIn("--require-hashes", command)
        self.assertIn("--isolated", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertFalse(kwargs["shell"])
        self.assertNotIn("PIP_INDEX_URL", kwargs["env"])
        self.assertTrue(result["hash_checking"])
        self.assertTrue(result["atomic_publish"])
        self.assertTrue((target / "installed.marker").is_file())
        self.assertEqual(Path(command[command.index("--target") + 1]).parent.resolve(), self.root.resolve())

    def test_real_offline_pip_install_from_synthetic_hashed_wheel(self):
        """Exercise pip itself with a valid local wheel and no package index."""
        if not importlib.util.find_spec("pip"):
            self.skipTest("pip is unavailable in this CI interpreter")
        wheelhouse = self.root / "real-wheelhouse"
        wheelhouse.mkdir()
        wheel = wheelhouse / "demo_portable-1.0-py3-none-any.whl"
        files = {
            "demo_portable/__init__.py": b"VALUE = 'installed-offline'\n",
            "demo_portable-1.0.dist-info/METADATA": (
                b"Metadata-Version: 2.1\nName: demo-portable\nVersion: 1.0\n\n"
            ),
            "demo_portable-1.0.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\nGenerator: yuki-test\nRoot-Is-Purelib: true\n"
                b"Tag: py3-none-any\n"
            ),
        }
        record_rows = []
        for name, payload in files.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
            record_rows.append((name, f"sha256={digest}", str(len(payload))))
        record_rows.append(("demo_portable-1.0.dist-info/RECORD", "", ""))
        record = io.StringIO()
        csv.writer(record, lineterminator="\n").writerows(record_rows)
        with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, payload in files.items():
                bundle.writestr(name, payload)
            bundle.writestr("demo_portable-1.0.dist-info/RECORD", record.getvalue())
        wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lock = self.root / "real-requirements.lock"
        lock.write_text(
            f"demo-portable==1.0 --hash=sha256:{wheel_digest}\n",
            encoding="utf-8",
        )
        runtime_root = Path(sys.executable).resolve().parent
        python = Path(sys.executable).resolve()
        target = self.root / "real-installed"
        result = install_offline_dependencies(
            runtime_root, python, lock, wheelhouse, target, timeout_seconds=120
        )
        self.assertTrue(result["atomic_publish"])
        self.assertTrue((target / "demo_portable" / "__init__.py").is_file())
        check = subprocess.run(
            [
                sys.executable, "-I", "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import demo_portable; "
                "assert demo_portable.VALUE == 'installed-offline'",
                str(target),
            ],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_pinned_pip_wheel_bootstraps_without_runtime_pip(self):
        """Run a pip-compatible module directly from a pinned wheel via zipimport."""
        self._write_lock()
        pip_wheel = self.root / "pip-99.0-py3-none-any.whl"
        pip_files = {
            "pip/__init__.py": b"__version__ = '99.0'\\n",
            "pip/_internal/__init__.py": b"",
            "pip/_internal/cli/__init__.py": b"",
            "pip/_internal/cli/main.py": (
                b"import pathlib, sys\\n"
                b"def main(args):\\n"
                b"    target = pathlib.Path(args[args.index('--target') + 1])\\n"
                b"    target.mkdir(parents=True, exist_ok=True)\\n"
                b"    (target / 'bootstrapped.marker').write_text('ok')\\n"
                b"    return 0\\n"
            ),
            "pip-99.0.dist-info/METADATA": (
                b"Metadata-Version: 2.1\\nName: pip\\nVersion: 99.0\\n\\n"
            ),
        }
        with zipfile.ZipFile(pip_wheel, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, payload in pip_files.items():
                bundle.writestr(name, payload)
        pip_digest = hashlib.sha256(pip_wheel.read_bytes()).hexdigest()
        runtime_root = Path(sys.executable).resolve().parent
        python = Path(sys.executable).resolve()
        target = self.root / "bootstrapped-site"
        result = install_offline_dependencies(
            runtime_root, python, self.lock, self.wheelhouse, target,
            pip_wheel=pip_wheel, pip_sha256=pip_digest, pip_version="99.0",
        )
        self.assertTrue((target / "bootstrapped.marker").is_file())
        self.assertEqual(result["pip_bootstrap"], "pinned-wheel")
        self.assertEqual(result["pip"]["version"], "99.0")

    def test_pip_bootstrap_rejects_digest_mismatch(self):
        pip_wheel = self.root / "pip-99.0-py3-none-any.whl"
        with zipfile.ZipFile(pip_wheel, "w") as bundle:
            bundle.writestr("pip/__init__.py", "")
            bundle.writestr("pip/_internal/cli/main.py", "")
            bundle.writestr("pip-99.0.dist-info/METADATA", "Name: pip\\nVersion: 99.0\\n")
        from scripts.portable_runtime_inputs import validate_pip_bootstrap_wheel
        with self.assertRaisesRegex(InputValidationError, "SHA-256 does not match"):
            validate_pip_bootstrap_wheel(pip_wheel, "0" * 64, "99.0")

    def test_installer_rejects_failed_pip_execution(self):
        self._write_lock()
        runtime_root = self.root / "verified-runtime"
        runtime_root.mkdir()
        python = runtime_root / "python"
        python.write_bytes(b"trusted interpreter placeholder")
        def failed_runner(command, **kwargs):
            staged_target = Path(command[command.index("--target") + 1])
            staged_target.mkdir(exist_ok=True)
            (staged_target / "partial.marker").write_text("partial", encoding="utf-8")
            return SimpleNamespace(returncode=1, stderr="bad wheel", stdout="")
        final_target = self.root / "site"
        with self.assertRaisesRegex(InputValidationError, "bad wheel"):
            install_offline_dependencies(runtime_root, python, self.lock, self.wheelhouse, final_target, runner=failed_runner)
        self.assertFalse(final_target.exists())
        self.assertEqual(list(self.root.glob(".yuki-dependencies-stage-*")), [])


    def test_cli_requires_all_installation_paths_together(self):
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = main([
                "--runtime-archive", "runtime.zip",
                "--runtime-sha256", "0" * 64,
                "--requirements-lock", "requirements.lock",
                "--wheelhouse", "wheelhouse",
                "--install-python", "runtime/python",
            ])
        self.assertEqual(result, 2)
        self.assertIn("must be supplied together", errors.getvalue())

    def test_installer_rejects_interpreter_outside_verified_runtime_root(self):
        self._write_lock()
        runtime_root = self.root / "verified-runtime"
        runtime_root.mkdir()
        external_python = self.root / "host-python"
        external_python.write_bytes(b"host interpreter placeholder")
        with self.assertRaisesRegex(InputValidationError, "inside the independently verified runtime root"):
            install_offline_dependencies(runtime_root, external_python, self.lock, self.wheelhouse, self.root / "site")


if __name__ == "__main__":
    unittest.main()
