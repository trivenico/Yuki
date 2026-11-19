import tempfile
import unittest
from pathlib import Path

from automation.sandbox_artifacts import ApprovedArtifactPromotion
from automation.sandbox_job_lifecycle import (
    JobState,
    SandboxJobError,
    SandboxJobLifecycle,
)


class SandboxJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.launch_calls = []
        self.freeze_calls = []\n        self.audit_events = []\n        self.audit_fails = False
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

        self.promotion = ApprovedArtifactPromotion(
            self.workspace,
            trusted_principal=lambda principal: principal.actor_id == "alice",
            approve_promotion=lambda *_: True,
            audit=lambda _event: None,
        )
        self.lifecycle = SandboxJobLifecycle(
            self.jobs, launcher=launch, freezer=freeze, promotion=self.promotion
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
        started = self.lifecycle.start_job(job.job_id)
        self.assertEqual(started.state, JobState.RUNNING)
        frozen = self.lifecycle.freeze_job(job.job_id)
        self.assertEqual(frozen.state, JobState.FROZEN)
        manifest = self.lifecycle.validate_job(job.job_id, allowed_extensions=(".txt",))
        self.assertEqual(len(manifest.files), 1)
        validated = self.lifecycle.get_job(job.job_id)
        self.assertEqual(validated.state, JobState.VALIDATED)
        self.lifecycle.promote_artifact(
            job.job_id, principal=self.principal,
            artifact_path="report.txt", destination_path="report.txt",
        )
        promoted = self.lifecycle.get_job(job.job_id)
        self.assertEqual(promoted.state, JobState.PROMOTED)
        self.assertEqual((self.workspace / "report.txt").read_text(encoding="utf-8"),
                         "sandbox output")
        cleaned = self.lifecycle.cleanup_job(job.job_id)
        self.assertEqual(cleaned.state, JobState.CLEANED)
        self.assertFalse(Path(job.staging_directory).exists())

    def test_failed_launch_can_only_be_cleaned_after_terminal_state(self):
        self.launch_fails = True
        job = self.lifecycle.create_job()
        with self.assertRaises(SandboxJobError):
            self.lifecycle.start_job(job.job_id)
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.FAILED)
        self.assertEqual(self.lifecycle.cleanup_job(job.job_id).state, JobState.CLEANED)

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
        self.assertEqual(
            actions,
            ["sandbox.job.create", "sandbox.job.start",
             "sandbox.job.freeze", "sandbox.job.validate"],
        )
        self.assertTrue(all(event["job_id"] == job.job_id for event in self.audit_events))

    def test_create_fails_closed_when_audit_is_unavailable(self):
        self.audit_fails = True
        with self.assertRaises(SandboxJobError):
            self.lifecycle.create_job()
        self.assertEqual(list(self.jobs.iterdir()), [])

    def test_start_audit_failure_stops_launched_runtime(self):
        job = self.lifecycle.create_job()
        self.audit_fails = True
        with self.assertRaises(SandboxJobError):
            self.lifecycle.start_job(job.job_id)
        self.assertEqual(self.freeze_calls, [{"container": job.job_id}])
        self.assertEqual(self.lifecycle.get_job(job.job_id).state, JobState.FAILED)

if __name__ == "__main__":
    unittest.main()
