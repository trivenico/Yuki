import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from automation.sandbox_artifacts import (
    ArtifactValidationError,
    validate_artifact_directory,
)


class SandboxArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_manifest_is_deterministic_and_contains_file_digests(self):
        (self.root / "b.txt").write_bytes(b"second")
        (self.root / "a.json").write_bytes(b'{"ok":true}')
        first = validate_artifact_directory(
            self.root, allowed_extensions=(".txt", ".json")
        )
        second = validate_artifact_directory(
            self.root, allowed_extensions=(".json", ".txt")
        )
        self.assertEqual(first, second)
        self.assertEqual([item.path for item in first.files], ["a.json", "b.txt"])
        self.assertEqual(first.files[0].sha256, hashlib.sha256(b'{"ok":true}').hexdigest())
        self.assertEqual(first.total_bytes, len(b'{"ok":true}') + len(b"second"))
        self.assertEqual(len(first.digest), 64)

    def test_rejects_unapproved_extension_and_secret_names(self):
        (self.root / "payload.exe").write_bytes(b"binary")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        (self.root / "payload.exe").unlink()
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".env", ".txt"))

    def test_rejects_symlink_and_hard_link(self):
        target = self.root / "target.txt"
        target.write_text("data", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        link.unlink()
        hardlink = self.root / "hardlink.txt"
        try:
            os.link(target, hardlink)
        except OSError:
            self.skipTest("hard links unavailable")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))

    def test_rejects_file_count_and_size_limits(self):
        (self.root / "a.txt").write_bytes(b"12345")
        (self.root / "b.txt").write_bytes(b"67890")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_files=1
            )
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_file_bytes=4
            )
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_total_bytes=9
            )

    def test_rejects_empty_directory_and_symlink_root(self):
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        alias = self.root.parent / (self.root.name + "-alias")
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        self.addCleanup(lambda: alias.unlink(missing_ok=True))
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(alias, allowed_extensions=(".txt",))

    def test_rejects_special_files_and_invalid_limits(self):
        fifo = self.root / "pipe.txt"
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unsupported")
        os.mkfifo(fifo)
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(self.root, allowed_extensions=(".txt",))
        fifo.unlink()
        (self.root / "ok.txt").write_text("ok", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            validate_artifact_directory(
                self.root, allowed_extensions=(".txt",), max_files=True
            )


if __name__ == "__main__":
    unittest.main()


class ApprovedArtifactPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staging = self.root / "staging"
        self.workspace = self.root / "workspace"
        self.staging.mkdir()
        self.workspace.mkdir()
        self.payload = self.staging / "report.txt"
        self.payload.write_text("trusted report", encoding="utf-8")
        self.manifest = validate_artifact_directory(
            self.staging, allowed_extensions=(".txt",)
        )
        from automation.authorized_read_tools import ToolPrincipal
        self.principal = ToolPrincipal("alice", frozenset({"workspace:write"}))
        self.approvals = []
        self.audit_events = []
        self.approved = True
        from automation.sandbox_artifacts import ApprovedArtifactPromotion
        self.service = ApprovedArtifactPromotion(
            self.workspace,
            trusted_principal=lambda p: p.actor_id == "alice",
            approve_promotion=lambda actor, promotion_id, digest: (
                self.approvals.append((actor, promotion_id, digest)) or self.approved
            ),
            audit=self.audit_events.append,
        )

    def test_promotes_exact_digest_to_exact_new_destination(self):
        (self.workspace / "reports").mkdir()
        digest = self.service.promote(
            principal=self.principal, staging_directory=self.staging,
            manifest=self.manifest, artifact_path="report.txt",
            destination_path="reports/report.txt",
        )
        target = self.workspace / "reports" / "report.txt"
        self.assertEqual(target.read_text(encoding="utf-8"), "trusted report")
        self.assertEqual(digest, self.approvals[0][2])
        self.assertEqual(self.audit_events[-1]["outcome"], "succeeded")

    def test_denied_approval_does_not_create_destination(self):
        self.approved = False
        with self.assertRaises(Exception):
            self.service.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=self.manifest, artifact_path="report.txt",
                destination_path="report.txt",
            )
        self.assertFalse((self.workspace / "report.txt").exists())

    def test_rejects_overwrite_traversal_and_manifest_mismatch(self):
        target = self.workspace / "existing.txt"
        target.write_text("keep", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            self.service.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=self.manifest, artifact_path="report.txt",
                destination_path="existing.txt",
            )
        with self.assertRaises(ArtifactValidationError):
            self.service.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=self.manifest, artifact_path="../report.txt",
                destination_path="new.txt",
            )
        self.payload.write_text("tampered", encoding="utf-8")
        with self.assertRaises(ArtifactValidationError):
            self.service.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=self.manifest, artifact_path="report.txt",
                destination_path="new.txt",
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_rejects_nul_and_backslash_paths(self):
        for unsafe_path in ("folder\\report.txt", "folder\x00report.txt"):
            with self.subTest(path=unsafe_path):
                with self.assertRaises(ArtifactValidationError):
                    self.service.promote(
                        principal=self.principal, staging_directory=self.staging,
                        manifest=self.manifest, artifact_path=unsafe_path,
                        destination_path="report.txt",
                    )

    def test_rejects_forged_manifest_digest(self):
        from dataclasses import replace
        forged = replace(self.manifest, digest="0" * 64)
        with self.assertRaises(ArtifactValidationError):
            self.service.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=forged, artifact_path="report.txt",
                destination_path="report.txt",
            )
        self.assertFalse((self.workspace / "report.txt").exists())

    def test_rejects_untrusted_principal_and_missing_permission(self):
        from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError
        with self.assertRaises(ToolAuthorizationError):
            self.service.promote(
                principal=ToolPrincipal("mallory", frozenset({"workspace:write"})),
                staging_directory=self.staging, manifest=self.manifest,
                artifact_path="report.txt", destination_path="report.txt",
            )
        with self.assertRaises(ToolAuthorizationError):
            self.service.promote(
                principal=ToolPrincipal("alice", frozenset()),
                staging_directory=self.staging, manifest=self.manifest,
                artifact_path="report.txt", destination_path="report.txt",
            )

    def test_audit_failure_rolls_back_created_file(self):
        from automation.sandbox_artifacts import ApprovedArtifactPromotion
        failing = ApprovedArtifactPromotion(
            self.workspace, trusted_principal=lambda p: True,
            approve_promotion=lambda *_: True,
            audit=lambda event: (_ for _ in ()).throw(OSError("audit unavailable"))
                if event["outcome"] == "succeeded" else None,
        )
        with self.assertRaises(RuntimeError):
            failing.promote(
                principal=self.principal, staging_directory=self.staging,
                manifest=self.manifest, artifact_path="report.txt",
                destination_path="report.txt",
            )
        self.assertFalse((self.workspace / "report.txt").exists())
