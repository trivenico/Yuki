import tempfile, unittest, threading
from pathlib import Path
from unittest.mock import patch
from core.coding_task_execution import CodingTaskExecutionError, CodingTaskExecutionStore, execute_approved_coding_task
from core.coding_task_runner import SandboxExecutionRequest, SandboxExecutionResult
from core.coding_tasks import CodingTaskStore
from core.sandbox_policy import SandboxPolicy

class CodingTaskExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); self.workspace = root / "workspace"; self.workspace.mkdir()
        self.tasks = CodingTaskStore(str(root / "tasks.sqlite3"))
        self.evidence = CodingTaskExecutionStore(str(root / "evidence.sqlite3"))
        task = self.tasks.create(idempotency_key="task-1", goal="Fix bug", workspace=str(self.workspace))
        task = self.tasks.attach_patch(task.task_id, expected_version=task.version,
            patch="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
        task = self.tasks.transition(task.task_id, expected_version=task.version, new_state="ready_for_review")
        self.task = self.tasks.transition(task.task_id, expected_version=task.version, new_state="approved",
                                          review="Human reviewed patch.")
    def call(self, **overrides):
        args = dict(task_store=self.tasks, evidence_store=self.evidence, policy=SandboxPolicy(),
            task_id=self.task.task_id, image="python:3.12", workspace=str(self.workspace),
            command=["pytest","-q"], approve=lambda task_id, patch_hash, request: True)
        args.update(overrides); return execute_approved_coding_task(**args)
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_persists_hash_only_evidence(self, run):
        run.return_value = SandboxExecutionResult(0,"tests passed\n",False,False,0.2)
        result,evidence = self.call(); saved = self.evidence.get(evidence.run_id)
        self.assertEqual(result.returncode,0); self.assertEqual(saved.output_sha256,evidence.output_sha256)
        self.assertFalse(hasattr(saved,"output"))
        self.assertEqual(self.tasks.get(self.task.task_id).state, "completed")
        request = SandboxExecutionRequest("python:3.12",str(self.workspace.resolve()),("pytest","-q"),30,512,1000,1048576)
        self.assertFalse(run.call_args.kwargs["approve"](request))
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_approval_binds_task_and_patch(self, run):
        run.return_value = SandboxExecutionResult(0,"ok",False,False,0.1); seen=[]
        self.call(approve=lambda tid,digest,req: seen.append((tid,digest,req)) or True)
        request = SandboxExecutionRequest("python:3.12",str(self.workspace.resolve()),("pytest","-q"),30,512,1000,1048576)
        self.assertFalse(run.call_args.kwargs["approve"](request))
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.tasks.get(self.task.task_id).state, "completed")
        self.assertEqual(seen[0][0],self.task.task_id); self.assertEqual(seen[0][1],self.task.patch_sha256)
        self.assertIsInstance(seen[0][2],SandboxExecutionRequest)
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_unapproved_task_denied(self, run):
        self.tasks.transition(self.task.task_id,expected_version=self.task.version,new_state="cancelled")
        with self.assertRaises(CodingTaskExecutionError): self.call()
        run.assert_not_called()
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_workspace_mismatch_denied(self, run):
        with self.assertRaises(CodingTaskExecutionError): self.call(workspace=str(Path(self.tmp.name)))
        run.assert_not_called()
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_command_allowlist(self, run):
        with self.assertRaises(CodingTaskExecutionError): self.call(command=["sh","-c","echo unsafe"])
        run.assert_not_called()
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_tampered_patch_denied(self, run):
        with patch.object(self.tasks,"patch_integrity_ok",return_value=False):
            with self.assertRaises(CodingTaskExecutionError): self.call()
        run.assert_not_called()

    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_runner_failure_leaves_durable_claim_and_blocks_replay(self, run):
        from core.coding_task_runner import SandboxExecutionError
        run.side_effect = SandboxExecutionError("simulated interruption")
        with self.assertRaises(CodingTaskExecutionError):
            self.call()
        self.assertEqual(self.tasks.get(self.task.task_id).state, "executing")
        run.reset_mock()
        with self.assertRaises(CodingTaskExecutionError):
            self.call()
        run.assert_not_called()

    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_evidence_failure_leaves_claimed_task_non_replayable(self, run):
        import sqlite3
        run.return_value = SandboxExecutionResult(0, "ok", False, False, 0.1)
        with patch.object(self.evidence, "record", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(CodingTaskExecutionError):
                self.call()
        self.assertEqual(self.tasks.get(self.task.task_id).state, "executing")
        run.reset_mock()
        with self.assertRaises(CodingTaskExecutionError):
            self.call()
        run.assert_not_called()

    def test_atomic_execution_claim_allows_only_one_winner(self):
        barrier = threading.Barrier(3)
        outcomes = []
        def claim():
            barrier.wait()
            try:
                self.tasks.claim_execution(
                    self.task.task_id, expected_version=self.task.version,
                    expected_patch_sha256=self.task.patch_sha256,
                )
                outcomes.append("claimed")
            except Exception:
                outcomes.append("denied")
        workers = [threading.Thread(target=claim) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(sorted(outcomes), ["claimed", "denied"])
        self.assertEqual(self.tasks.get(self.task.task_id).state, "executing")


    def test_unresolved_execution_claim_cannot_be_deleted(self):
        claimed = self.tasks.claim_execution(
            self.task.task_id, expected_version=self.task.version,
            expected_patch_sha256=self.task.patch_sha256,
        )
        with self.assertRaises(Exception):
            self.tasks.delete(claimed.task_id, expected_version=claimed.version)
        self.assertEqual(self.tasks.get(claimed.task_id).state, "executing")
\nif __name__ == "__main__": unittest.main()
