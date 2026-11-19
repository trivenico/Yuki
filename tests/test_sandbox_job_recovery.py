import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.sandbox_job_recovery import SandboxJobRecovery
from automation.sandbox_job_store import SandboxJobStore


@unittest.skipUnless(os.name == "posix", "sandbox job recovery ownership requires POSIX UID metadata")
class SandboxJobRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = (self.root / "jobs").resolve()
        self.jobs.mkdir(mode=0o700)
        os.chmod(self.jobs, 0o700)
        self.dbroot = self.root / "db"
        self.dbroot.mkdir(mode=0o700)
        os.chmod(self.dbroot, 0o700)
        self.store = SandboxJobStore(self.dbroot / "jobs.sqlite3")
        self.job_id = "job_" + "c" * 32
        self.staging = self.jobs / self.job_id
        self.staging.mkdir(mode=0o700)
        self.store.create(
            job_id=self.job_id,
            staging_directory=str(self.staging),
            container_name="yuki-job-" + "c" * 32,
        )
        self.store.transition(
            self.job_id, expected_states=("created",), new_state="launching",
            container_name="yuki-job-" + "c" * 32,
        )
        self.runtime = Mock()
        self.runtime.handle_for_job.return_value = object()
        self.runtime.reconcile.return_value = "removed"
        self.events = []
        self.recovery = SandboxJobRecovery(
            store=self.store, runtime=self.runtime, jobs_root=self.jobs,
            audit=lambda event: self.events.append(dict(event)),
        )

    def test_reconciles_launching_job_and_persists_failed_after_container_removed(self):
        outcomes = self.recovery.recover_nonterminal()
        self.assertEqual(outcomes[0]["outcome"], "removed")
        self.assertEqual(self.store.get(self.job_id).state, "failed")
        self.runtime.reconcile.assert_called_once()
        self.assertEqual(self.events[0]["action"], "sandbox.job.recovery")
        self.assertTrue(self.staging.is_dir())

    def test_docker_unavailable_preserves_state_and_staging(self):
        self.runtime.reconcile.side_effect = RuntimeError("daemon unavailable")
        outcomes = self.recovery.recover_nonterminal()
        self.assertEqual(outcomes[0]["outcome"], "recovery_required")
        self.assertEqual(self.store.get(self.job_id).state, "launching")
        self.assertTrue(self.staging.is_dir())
        self.assertEqual(self.events, [])

    def test_audit_failure_preserves_durable_state_and_staging(self):
        recovery = SandboxJobRecovery(
            store=self.store, runtime=self.runtime, jobs_root=self.jobs,
            audit=lambda _event: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        outcomes = recovery.recover_nonterminal()
        self.assertEqual(outcomes[0]["outcome"], "recovery_required")
        self.assertEqual(self.store.get(self.job_id).state, "launching")
        self.assertTrue(self.staging.is_dir())

    def test_rejects_staging_symlink_without_touching_runtime(self):
        self.staging.rmdir()
        target = self.root / "target"
        target.mkdir()
        self.staging.symlink_to(target, target_is_directory=True)
        outcomes = self.recovery.recover_nonterminal()
        self.assertEqual(outcomes[0]["outcome"], "recovery_required")
        self.runtime.reconcile.assert_not_called()
        self.assertEqual(self.store.get(self.job_id).state, "launching")

    def test_defers_created_and_frozen_jobs(self):
        self.store.transition(
            self.job_id, expected_states=("launching",), new_state="running"
        )
        self.store.transition(
            self.job_id, expected_states=("running",), new_state="frozen"
        )
        outcomes = self.recovery.recover_nonterminal()
        self.assertEqual(outcomes[0]["outcome"], "deferred")
        self.runtime.reconcile.assert_not_called()
        self.assertEqual(self.store.get(self.job_id).state, "frozen")


if __name__ == "__main__":
    unittest.main()
