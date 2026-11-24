import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from core.coding_task_execution import CodingTaskExecutionError, CodingTaskExecutionStore, execute_approved_coding_task
from core.coding_task_runner import SandboxExecutionRequest, SandboxExecutionResult
from core.coding_tasks import CodingTaskStore
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
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
        request = SandboxExecutionRequest("python:3.12",str(self.workspace.resolve()),("pytest","-q"),30,512,1000,1048576)
        self.assertTrue(run.call_args.kwargs["approve"](request))
    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_approval_binds_task_and_patch(self, run):
        run.return_value = SandboxExecutionResult(0,"ok",False,False,0.1); seen=[]
        self.call(approve=lambda tid,digest,req: seen.append((tid,digest,req)) or True)
        request = SandboxExecutionRequest("python:3.12",str(self.workspace.resolve()),("pytest","-q"),30,512,1000,1048576)
        self.assertTrue(run.call_args.kwargs["approve"](request))
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
    def test_central_gateway_authorizes_exact_envelope_before_launch(self, run):
        class Policy:
            def __init__(self):
                self.requests = []
            def authorize(self, request):
                self.requests.append(request)
                return AuthorizationDecision(True, "decision-1", "policy-v1", "allowed")

        policy = Policy()
        audit = []
        gateway = AuthorizationGateway(policy, audit=lambda req, decision: audit.append((req, decision)))
        launched = []
        def fake_runner(policy_arg, **kwargs):
            request = SandboxExecutionRequest(
                "python:3.12", str(self.workspace.resolve()), ("pytest", "-q"),
                policy_arg.timeout_seconds, policy_arg.memory_mb,
                policy_arg.cpu_millis, policy_arg.max_output_bytes,
            )
            self.assertTrue(kwargs["approve"](request))
            launched.append(True)
            return SandboxExecutionResult(0, "ok", False, False, 0.1)
        run.side_effect = fake_runner
        result, evidence = self.call(
            authorization_gateway=gateway,
            principal=Principal("alice", "human", tenant_id="tenant-1"),
            policy_revision="policy-v1",
            delegation_id="grant-9",
            approval_id="approval-5",
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(launched)
        request = policy.requests[0]
        self.assertEqual(request.action, "sandbox:execute")
        self.assertTrue(request.resource.startswith("sandbox:sha256:"))
        self.assertEqual(len(request.resource), len("sandbox:sha256:") + 64)
        self.assertEqual(request.risk, "high")
        self.assertEqual(request.task_id, self.task.task_id)
        self.assertEqual(request.delegation_id, "grant-9")
        self.assertEqual(request.approval_id, "approval-5")
        self.assertEqual(request.expected_policy_revision, "policy-v1")
        self.assertEqual(len(audit), 1)

    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_central_gateway_denial_prevents_process_launch(self, run):
        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(False, "decision-2", "policy-v1", "denied")

        gateway = AuthorizationGateway(Policy(), audit=lambda *_: None)
        launched = []
        def fake_runner(policy_arg, **kwargs):
            request = SandboxExecutionRequest(
                "python:3.12", str(self.workspace.resolve()), ("pytest", "-q"),
                policy_arg.timeout_seconds, policy_arg.memory_mb,
                policy_arg.cpu_millis, policy_arg.max_output_bytes,
            )
            kwargs["approve"](request)
            launched.append(True)
            return SandboxExecutionResult(0, "should not run", False, False, 0.1)
        run.side_effect = fake_runner
        with self.assertRaises(CodingTaskExecutionError):
            self.call(
                authorization_gateway=gateway,
                principal=Principal("alice", "human"),
                policy_revision="policy-v1",
            )
        self.assertEqual(launched, [])

    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_gateway_and_principal_are_required_as_a_pair(self, run):
        with self.assertRaises(CodingTaskExecutionError):
            self.call(authorization_gateway=object())
        run.assert_not_called()

    @patch("core.coding_task_execution.run_sandboxed_task")
    def test_central_audit_failure_prevents_process_launch(self, run):
        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision-3", "policy-v1", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda *_: False)
        launched = []
        def fake_runner(policy_arg, **kwargs):
            request = SandboxExecutionRequest(
                "python:3.12", str(self.workspace.resolve()), ("pytest", "-q"),
                policy_arg.timeout_seconds, policy_arg.memory_mb,
                policy_arg.cpu_millis, policy_arg.max_output_bytes,
            )
            kwargs["approve"](request)
            launched.append(True)
        run.side_effect = fake_runner
        with self.assertRaises(CodingTaskExecutionError):
            self.call(
                authorization_gateway=gateway,
                principal=Principal("alice", "human"),
                policy_revision="policy-v1",
            )
        self.assertEqual(launched, [])

if __name__ == "__main__": unittest.main()
