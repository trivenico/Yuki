"""Tests for the portable release-candidate orchestration boundary."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.portable_release_build import build_release_candidate
from scripts.portable_runtime_inputs import InputValidationError


class PortableReleaseBuildTests(unittest.TestCase):
    def test_builds_unsigned_candidate_and_keeps_release_gates_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            output_parent = root / "output"
            output_parent.mkdir()
            output = output_parent / "release"
            calls = []

            def assemble_runtime(**kwargs):
                calls.append("runtime")
                kwargs["runtime_root"].mkdir()
                kwargs["dependency_target"].mkdir()
                return {"network_downloads": False, "application_executed": False}

            def assemble_bundle(runtime, dependencies, bundle):
                calls.append("bundle")
                bundle.mkdir()
                (bundle / "runtime").mkdir()
                (bundle / "site-packages").mkdir()
                return {"signed": False}

            def manifest(bundle, **kwargs):
                calls.append("manifest")
                self.assertTrue((bundle / "portable_entry.py").is_file())
                return {"signed": False}

            def audit(bundle, platform):
                calls.append("audit")
                self.assertEqual(platform, "linux")
                return {"metadata_failures": 0, "resolution_fully_verified": False}

            with (
                patch("scripts.portable_release_build._host_audit_platform", return_value="linux"),
                patch("scripts.portable_release_build.assemble_portable_runtime", side_effect=assemble_runtime),
                patch("scripts.portable_release_build.assemble_bundle", side_effect=assemble_bundle),
                patch("scripts.portable_release_build.write_unsigned_manifest", side_effect=manifest),
                patch("scripts.portable_release_build.audit_bundle", side_effect=audit),
            ):
                report = build_release_candidate(
                    runtime_archive=root / "runtime.zip",
                    runtime_sha256="a" * 64,
                    python_relative_path="bin/python",
                    requirements_lock=root / "requirements.lock",
                    wheelhouse=root / "wheelhouse",
                    pip_wheel=root / "pip.whl",
                    pip_sha256="b" * 64,
                    pip_version="25.0",
                    work_root=work,
                    output_root=output,
                    platform_tag="linux-x86_64",
                    runtime_version="3.12.9",
                )

            self.assertEqual(calls, ["runtime", "bundle", "manifest", "audit"])
            self.assertFalse(report["signed"])
            self.assertFalse(report["release_ready"])
            self.assertGreater(len(report["release_blockers"]), 0)
            self.assertTrue((output / "yuki-portable" / "portable_entry.py").is_file())

    def test_removes_partial_output_when_native_audit_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            output_parent = root / "output"
            output_parent.mkdir()
            output = output_parent / "release"

            def assemble_runtime(**kwargs):
                kwargs["runtime_root"].mkdir()
                kwargs["dependency_target"].mkdir()
                return {}

            def assemble_bundle(runtime, dependencies, bundle):
                bundle.mkdir()
                (bundle / "runtime").mkdir()
                (bundle / "site-packages").mkdir()
                return {}

            with (
                patch("scripts.portable_release_build._host_audit_platform", return_value="linux"),
                patch("scripts.portable_release_build.assemble_portable_runtime", side_effect=assemble_runtime),
                patch("scripts.portable_release_build.assemble_bundle", side_effect=assemble_bundle),
                patch("scripts.portable_release_build.write_unsigned_manifest", return_value={}),
                patch("scripts.portable_release_build.audit_bundle", return_value={"metadata_failures": 1}),
            ):
                with self.assertRaisesRegex(Exception, "release-blocking"):
                    build_release_candidate(
                        runtime_archive=root / "runtime.zip",
                        runtime_sha256="a" * 64,
                        python_relative_path="bin/python",
                        requirements_lock=root / "requirements.lock",
                        wheelhouse=root / "wheelhouse",
                        pip_wheel=root / "pip.whl",
                        pip_sha256="b" * 64,
                        pip_version="25.0",
                        work_root=work,
                        output_root=output,
                        platform_tag="linux-x86_64",
                        runtime_version="3.12.9",
                    )
            self.assertFalse(output.exists())

    def test_rejects_cross_platform_target_before_building(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            output_parent = root / "output"
            output_parent.mkdir()
            with patch("scripts.portable_release_build._host_audit_platform", return_value="linux"):
                with self.assertRaisesRegex(InputValidationError, "must match target platform"):
                    build_release_candidate(
                        runtime_archive=root / "runtime.zip",
                        runtime_sha256="a" * 64,
                        python_relative_path="bin/python",
                        requirements_lock=root / "requirements.lock",
                        wheelhouse=root / "wheelhouse",
                        pip_wheel=root / "pip.whl",
                        pip_sha256="b" * 64,
                        pip_version="25.0",
                        work_root=work,
                        output_root=output_parent / "release",
                        platform_tag="windows-x86_64",
                        runtime_version="3.12.9",
                    )
            self.assertFalse((work / "verified-inputs").exists())


if __name__ == "__main__":
    unittest.main()
