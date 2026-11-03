import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from core.skill_execution_manifest import validate_manifest
from core.skill_staging import SkillStagingError, stage_skill_artifact


def make_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return stream.getvalue()


class SkillStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.entries = [("package/__init__.py", b""), ("package/entry.py", b"def run(): return 1")]
        self.artifact = make_zip(self.entries)
        self.manifest = validate_manifest({
            "protocol_version": 1, "skill_id": "report_builder", "skill_version": "1.0.0",
            "artifact_sha256": hashlib.sha256(self.artifact).hexdigest(),
            "entrypoint": "package.entry:run", "input_schema_sha256": "b"*64,
            "output_schema_sha256": "c"*64,
            "runtime_image": "registry.example/yuki-runtime@sha256:"+"d"*64,
            "environment_lock_sha256": "e"*64, "capabilities": ["read_staged_input"],
            "resource_limits": {"timeout_seconds":30,"memory_mb":256,"cpu_millis":500,
                "max_processes":16,"max_input_bytes":65536,"max_output_bytes":262144},
            "network":"none","mount_policy":"invocation_staging_only",
        })

    def test_stages_verified_artifact_and_returns_stable_tree_digest(self):
        with stage_skill_artifact(self.artifact, self.manifest, staging_parent=self.tmp.name) as staged:
            self.assertEqual(staged.artifact_sha256, self.manifest.artifact_sha256)
            self.assertEqual(staged.file_count, 2)
            self.assertTrue((staged.root / "package/entry.py").is_file())
            self.assertEqual(len(staged.tree_sha256), 64)
            if os.name != "nt":
                self.assertFalse((staged.root / "package/entry.py").stat().st_mode & 0o222)
            else:
                # Windows chmod/stat does not expose POSIX write-bit semantics.
                self.assertTrue((staged.root / "package/entry.py").is_file())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_rejects_changed_artifact_before_staging(self):
        with self.assertRaises(SkillStagingError):
            with stage_skill_artifact(self.artifact+b"x", self.manifest, staging_parent=self.tmp.name):
                self.fail("must not stage tampered artifact")

    def test_rejects_path_traversal_and_absolute_paths(self):
        for name in ("../escape.py", "/absolute.py", "C:/drive.py", "pkg\\escape.py"):
            artifact = make_zip([(name, b"bad")])
            manifest = validate_manifest(dict(self.manifest.as_dict(), artifact_sha256=hashlib.sha256(artifact).hexdigest()))
            with self.subTest(name=name), self.assertRaises(SkillStagingError):
                with stage_skill_artifact(artifact, manifest, staging_parent=self.tmp.name):
                    self.fail("unsafe path staged")

    def test_rejects_duplicate_and_case_colliding_paths(self):
        for entries in (
            [("package/entry.py", b"a"), ("package/entry.py", b"b")],
            [("package/entry.py", b"a"), ("PACKAGE/ENTRY.PY", b"b")],
        ):
            artifact = make_zip(entries)
            manifest = validate_manifest(dict(self.manifest.as_dict(), artifact_sha256=hashlib.sha256(artifact).hexdigest()))
            with self.assertRaises(SkillStagingError):
                with stage_skill_artifact(artifact, manifest, staging_parent=self.tmp.name):
                    self.fail("duplicate paths staged")

    def test_rejects_unicode_normalization_collisions(self):
        artifact = make_zip([
            ("package/café.py", b"first"),
            ("package/cafe\u0301.py", b"second"),
        ])
        manifest = validate_manifest(dict(
            self.manifest.as_dict(),
            artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        ))
        with self.assertRaises(SkillStagingError):
            with stage_skill_artifact(artifact, manifest, staging_parent=self.tmp.name):
                self.fail("Unicode-equivalent paths staged")

    def test_rejects_missing_entrypoint(self):
        artifact = make_zip([("package/other.py", b"pass")])
        manifest = validate_manifest(dict(self.manifest.as_dict(), artifact_sha256=hashlib.sha256(artifact).hexdigest()))
        with self.assertRaises(SkillStagingError):
            with stage_skill_artifact(artifact, manifest, staging_parent=self.tmp.name):
                self.fail("missing entrypoint staged")

    def test_rejects_symlink_entries(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            info = zipfile.ZipInfo("package/evil.py")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, b"../../outside")
        artifact = stream.getvalue()
        manifest = validate_manifest(dict(self.manifest.as_dict(), artifact_sha256=hashlib.sha256(artifact).hexdigest()))
        with self.assertRaises(SkillStagingError):
            with stage_skill_artifact(artifact, manifest, staging_parent=self.tmp.name):
                self.fail("symlink staged")

    @unittest.skipIf(os.name == "nt", "POSIX ownership and mode policy")
    def test_rejects_group_or_world_writable_staging_parent(self):
        insecure = Path(self.tmp.name) / "insecure"
        insecure.mkdir()
        insecure.chmod(0o777)
        try:
            with self.assertRaises(SkillStagingError):
                with stage_skill_artifact(self.artifact, self.manifest, staging_parent=insecure):
                    self.fail("insecure staging parent accepted")
        finally:
            insecure.chmod(0o700)

    def test_rejects_symlink_staging_parent(self):
        link = Path(self.tmp.name) / "link"
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(SkillStagingError):
            with stage_skill_artifact(self.artifact, self.manifest, staging_parent=link):
                self.fail("symlink staging parent accepted")


if __name__ == "__main__":
    unittest.main()
