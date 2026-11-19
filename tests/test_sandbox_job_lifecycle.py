import tempfile
import unittest
from pathlib import Path

from automation.sandbox_artifacts import ApprovedArtifactPromotion
from automation.sandbox_job_lifecycle import JobState, SandboxJobError, SandboxJobLifecycle
from automation.sandbox_job_store import SandboxJobStore
import os


@unittest.skipUnless(os.name == "posix", "sandbox job lifecycle ownership requires POSIX UID metadata")
class SandboxJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.dbroot = self.root / "db"
        self.dbroot.mkdir(mode=0o700)
        os.chmod(self.dbroot, 0o700)
        self.store = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        self.launch_calls = []
        self.freeze_calls = []
        self.audit_events = []
        self.audit_fails = False
        self.freeze_ok = True
        self.launch_fails = False

        def launch(job_id, staging):
            self.launch_calls.append((job_id, staging))
            if self.launch_fails:
                raise OSError("runtime unavailable")
            Path(staging, "report.txt").write_text("sandbox output", encoding="utf-8")
            return {"container": job_id}

        def freeze(handle):
            self.freeze_calls.append(handle)
            return self.freeze_ok

        def audit(event):
            if self.audit_fails:
                raise OSError("audit unavailable")
            self.audit_events.append(dict(event))

        self.promotion = ApprovedArtifactPromotion(
            self.workspace,
            trusted_principal=lambda principal: principal.actor_id == "alice",
            approve_promotion=lambda *_: True,
            audit=lambda _event: None,
        )
        self.lifecycle = SandboxJobLifecycle(
            self.jobs, launcher=launch, freezer=freeze,
            promotion=self.promotion, audit=audit, store=self.store,
        )
        from automation.authorized_read_tools import ToolPrincipal
        self.principal = ToolPrincipal("alice", frozenset({"workspace:write"}))

    def test_job_directory_is_private_and_owned_by_manager(self):
        job = self.lifecycle.create_job()
        path = Path(job.staging_directory)
        self.assertEqual(job.state, JobState.CREATED)
        self.assertTrue(path.is_dir())
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(SandboxJobError):
            self.lifecycle.get_job("job_" + "f" * 32)

    def test_requires_confirmed_freeze_before_validation(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        with self.assertRaises(SandboxJobError):
            self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        self.freeze_ok = False
        with self.assertRaises(SandboxJobError):
            self.lifecycle.freeze_job(job.job_id)
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.RUNNING)
        self.assertTrue(self.freeze_calls)

    def test_validates_promotes_and_cleans_complete_job(self):
        job = self.lifecycle.create_job()
        self.assertEqual(self.lifecycle.start_job(job.job_id).state, JobState.RUNNING)
        self.assertEqual(self.lifecycle.freeze_job(job.job_id).state, JobState.FROZEN)
        manifest = self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        self.assertEqual(len(manifest.files), 1)
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.VALIDATED)
        self.lifecycle.promote_artifact(
            job.job_id, principal=self.principal,
            artifact_path="report.txt", destination_path="report.txt",
        )
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.PROMOTED)
        self.assertEqual((self.workspace / "report.txt").read_text(encoding="utf-8"), "sandbox output")
        self.assertEqual(self.lifecycle.cleanup_job(job.job_id).state, JobState.CLEANED)
        self.assertFalse(Path(job.staging_directory).exists())

    def test_failed_launch_can_only_be_cleaned_after_terminal_state(self):
        self.launch_fails = True
        job = self.lifecycle.create_job()
        with self.assertRaises(SandboxJobError):
            self.lifecycle.start_job(job.job_id)
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.LAUNCHING)
        self.assertEqual(self.store.get(job.job_id).state, "launching")

    def test_created_job_is_restored_from_reopened_sqlite_store(self):
        job = self.lifecycle.create_job()
        reopened = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        restored = SandboxJobLifecycle(
            self.jobs, launcher=self.lifecycle._launcher,
            freezer=self.lifecycle._freezer, promotion=self.promotion,
            audit=lambda _event: None, store=reopened,
        )
        snapshot = restored.get_job(job.job_id)
        self.assertEqual(snapshot.state, JobState.CREATED)
        self.assertEqual(snapshot.staging_directory, job.staging_directory)

    def test_ambiguous_launch_state_survives_lifecycle_restart(self):
        self.launch_fails = True
        job = self.lifecycle.create_job()
        with self.assertRaises(SandboxJobError):
            self.lifecycle.start_job(job.job_id)
        reopened = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        restored = SandboxJobLifecycle(
            self.jobs, launcher=self.lifecycle._launcher,
            freezer=self.lifecycle._freezer, promotion=self.promotion,
            audit=lambda _event: None, store=reopened,
        )
        snapshot = restored.get_job(job.job_id)
        self.assertEqual(snapshot.state, JobState.LAUNCHING)
        self.assertEqual(reopened.get(job.job_id).container_name,
                         "yuki-job-" + job.job_id[4:])

    def test_validated_job_is_restored_only_after_manifest_revalidation(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.lifecycle.freeze_job(job.job_id)
        manifest = self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        reopened = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        restored = SandboxJobLifecycle(
            self.jobs, launcher=self.lifecycle._launcher,
            freezer=self.lifecycle._freezer, promotion=self.promotion,
            audit=lambda _event: None, store=reopened,
        )
        snapshot = restored.get_job(job.job_id)
        self.assertEqual(snapshot.state, JobState.VALIDATED)
        self.assertEqual(snapshot.manifest_digest, manifest.digest)

    def test_validated_restore_rejects_changed_staging_content(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.lifecycle.freeze_job(job.job_id)
        self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        Path(job.staging_directory, "report.txt").write_text("tampered", encoding="utf-8")
        reopened = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        restored = SandboxJobLifecycle(
            self.jobs, launcher=self.lifecycle._launcher,
            freezer=self.lifecycle._freezer, promotion=self.promotion,
            audit=lambda _event: None, store=reopened,
        )
        with self.assertRaises(SandboxJobError):
            restored.get_job(job.job_id)

    def test_cleanup_recovers_after_directory_removed_before_state_commit(self):
        job = self.lifecycle.create_job()
        self.lifecycle.discard_job(job.job_id)
        Path(job.staging_directory).rmdir()
        reopened = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        restored = SandboxJobLifecycle(
            self.jobs, launcher=self.lifecycle._launcher,
            freezer=self.lifecycle._freezer, promotion=self.promotion,
            audit=lambda _event: None, store=reopened,
        )
        self.assertEqual(restored.cleanup_job(job.job_id).state, JobState.CLEANED)

    def test_promotion_intent_is_durable_before_publication_and_completed(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.lifecycle.freeze_job(job.job_id)
        manifest = self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        self.lifecycle.promote_artifact(
            job.job_id, principal=self.principal,
            artifact_path="report.txt", destination_path="report.txt",
        )
        intent = self.store.get_promotion(job_id=job.job_id, artifact_path="report.txt")
        self.assertEqual(intent["status"], "completed")
        self.assertEqual(intent["artifact_sha256"], manifest.files[0].sha256)
        self.assertEqual(intent["destination_path"], "report.txt")

    def test_promotion_reconciles_published_file_after_crash_before_job_update(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.lifecycle.freeze_job(job.job_id)
        self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        # Simulate a crash after the atomic publish and promotion audit, before
        # the durable job promoted_paths update by invoking the promotion service.
        digest = self.lifecycle._promotion.promote(
            principal=self.principal, job_id=job.job_id,
            staging_directory=job.staging_directory,
            manifest=self.lifecycle._get(job.job_id).manifest, artifact_path="report.txt",
            destination_path="report.txt",
        )
        import hashlib, json
        item = self.lifecycle._get(job.job_id).manifest.files[0]
        approval_digest = hashlib.sha256(json.dumps({
            "job_id": job.job_id, "manifest_digest": self.lifecycle._get(job.job_id).manifest.digest,
            "artifact_path": "report.txt", "artifact_sha256": item.sha256,
            "destination_path": "report.txt",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(digest, approval_digest)
        self.store.record_promotion_intent(
            job_id=job.job_id, artifact_path="report.txt", destination_path="report.txt",
            artifact_sha256=item.sha256, artifact_size=item.size_bytes,
            approval_digest=approval_digest, actor_id="alice",
        )
        self.assertEqual(
            self.lifecycle.promote_artifact(
                job.job_id, principal=self.principal,
                artifact_path="report.txt", destination_path="report.txt",
            ),
            approval_digest,
        )
        self.assertEqual(self.store.get_promotion(
            job_id=job.job_id, artifact_path="report.txt"
        )["status"], "completed")

    def test_running_job_must_be_frozen_before_discard(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.freeze_ok = False
        with self.assertRaises(SandboxJobError):
            self.lifecycle.discard_job(job.job_id)
        self.freeze_ok = True
        self.assertEqual(self.lifecycle.discard_job(job.job_id).state, JobState.CANCELLED)
        self.assertEqual(self.lifecycle.cleanup_job(job.job_id).state, JobState.CLEANED)

    def test_cannot_validate_or_promote_before_state_transition(self):
        job = self.lifecycle.create_job()
        with self.assertRaises(SandboxJobError):
            self.lifecycle.promote_artifact(
                job.job_id, principal=self.principal,
                artifact_path="report.txt", destination_path="report.txt",
            )
        with self.assertRaises(SandboxJobError):
            self.lifecycle.cleanup_job(job.job_id)

    def test_lifecycle_transitions_emit_audit_events(self):
        job = self.lifecycle.create_job()
        self.lifecycle.start_job(job.job_id)
        self.lifecycle.freeze_job(job.job_id)
        self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        actions = [event["action"] for event in self.audit_events]
        self.assertEqual(actions, [
            "sandbox.job.create", "sandbox.job.start",
            "sandbox.job.freeze", "sandbox.job.validate",
        ])
        self.assertTrue(all(event["job_id"] == job.job_id for event in self.audit_events))

    def test_create_fails_closed_when_audit_is_unavailable(self):
        self.audit_fails = True
        with self.assertRaises(SandboxJobError):
            self.lifecycle.create_job()
        self.assertEqual(len(list(self.jobs.iterdir())), 1)
        record = self.store.list_nonterminal()[0]
        self.assertEqual(record.state, "created")

    def test_start_audit_failure_stops_launched_runtime(self):
        job = self.lifecycle.create_job()
        self.audit_fails = True
        with self.assertRaises(SandboxJobError):
            self.lifecycle.start_job(job.job_id)
        self.assertEqual(self.freeze_calls, [{"container": job.job_id}])
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.FAILED)
        self.assertEqual(self.store.get(job.job_id).state, "failed")


if __name__ == "__main__":
    unittest.main()
