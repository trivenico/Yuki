import asyncio
import json
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace

from api.projects import AuthenticatedProjectAPI
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.project_registry import ProjectRegistry
from core.task_evidence import EvidenceAttestation, TaskEvidenceStore
from integrations.github_actions_artifacts import GitHubActionsArtifactVerifier
from integrations.github_actions_dispatch import GitHubActionsDispatchLedger, GitHubActionsTaskDispatcher


class _Auth:
    def resolve_session(self, token):
        if token == "alice-token":
            return SimpleNamespace(user_id="alice")
        if token == "bob-token":
            return SimpleNamespace(user_id="bob")
        return None


class _App:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class AuthenticatedProjectAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.tasks = TaskRuntime(store, authorize=lambda actor, action, task: action == "task:read")
        self.tasks.get = lambda **kwargs: SimpleNamespace(
            task_id=kwargs["task_id"], owner_id=kwargs["actor_id"], state="running",
            updated_at="2026-11-23T00:00:00+00:00", dependencies=(),
        )
        self.registry = ProjectRegistry(
            str(Path(self.tmp.name) / "projects.sqlite3"),
            task_owner_resolver=lambda owner, task: self.tasks.get(actor_id=owner, task_id=task).owner_id,
        )
        self.app = _App()
        self.api = AuthenticatedProjectAPI(self.app, _Auth(), self.registry, self.tasks)

    def request(self, path, *, method="GET", token="alice-token", payload=None):
        sent = []
        body = json.dumps(payload).encode() if payload is not None else b""
        consumed = False

        async def receive():
            nonlocal consumed
            if consumed:
                return {"type": "http.request", "body": b"", "more_body": False}
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            sent.append(message)

        headers = [] if token is None else [(b"authorization", ("Bearer " + token).encode())]
        asyncio.run(self.api({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
        return sent[0]["status"], json.loads(sent[1]["body"]) if len(sent) > 1 and sent[1]["body"] else {}

    def test_create_and_list_project_uses_session_owner(self):
        status, created = self.request("/v1/projects", method="POST", payload={"name": "Yuki"})
        self.assertEqual(status, 201)
        project_id = created["project"]["project_id"]
        self.assertTrue(project_id.startswith("prj_"))
        status, listed = self.request("/v1/projects")
        self.assertEqual(status, 200)
        self.assertEqual([p["project_id"] for p in listed["projects"]], [project_id])
        self.assertEqual(self.request("/v1/projects", token="bob-token")[1]["projects"], [])

    def test_task_membership_uses_owner_scoped_task_resolver(self):
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Yuki"})
        project_id = created["project"]["project_id"]
        status, added = self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": "task-1"})
        self.assertEqual(status, 201)
        self.assertTrue(added["added"])
        status, tasks = self.request(f"/v1/projects/{project_id}/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(tasks["task_ids"], ["task-1"])

    def test_project_progress_is_derived_from_authorized_task_metadata(self):
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Yuki"})
        project_id = created["project"]["project_id"]
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": "task-1"})
        status, result = self.request(f"/v1/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(result["progress"]["total_tasks"], 1)
        self.assertEqual(result["progress"]["verified_completed"], 0)


    def test_project_progress_counts_only_trusted_task_evidence(self):
        evidence = TaskEvidenceStore(
            str(Path(self.tmp.name) / "verified-evidence.sqlite3"),
            attestation_verifier=lambda item: item.verifier_id == "trusted-ci",
        )
        evidence.record_verified(EvidenceAttestation(
            owner_id="alice", task_id="task-verified", task_version=7, kind="ci_workflow",
            reference="ci:run/42", digest_sha256="b" * 64,
            verifier_id="trusted-ci", verified_at="2026-11-23T00:00:00+00:00",
        ))
        evidence.record_verified(EvidenceAttestation(
            owner_id="alice", task_id="task-verified", task_version=6, kind="test_report",
            reference="test-report:old-attempt", digest_sha256="c" * 64,
            verifier_id="trusted-ci", verified_at="2026-11-22T00:00:00+00:00",
        ))
        self.tasks.get = lambda **kwargs: SimpleNamespace(
            task_id=kwargs["task_id"], owner_id=kwargs["actor_id"], state="succeeded", version=7,
            updated_at="2026-11-23T00:00:00+00:00", dependencies=(),
        )
        self.api = AuthenticatedProjectAPI(self.app, _Auth(), self.registry, self.tasks, evidence)
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Verified"})
        project_id = created["project"]["project_id"]
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": "task-verified"})
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": "task-unverified"})
        status, result = self.request(f"/v1/projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(result["progress"]["verified_completed"], 1)
        self.assertEqual(result["progress"]["succeeded_unverified"], 1)
        self.assertEqual(result["progress"]["measured_progress_percent"], 50.0)
        status, provenance = self.request(f"/v1/projects/{project_id}/evidence")
        self.assertEqual(status, 200)
        self.assertEqual([item["reference"] for item in provenance["evidence"]], ["ci:run/42"])
        self.assertEqual(provenance["evidence"][0]["task_version"], 7)

    def test_owner_authenticated_project_task_can_verify_its_run_artifact(self):
        accepted = {
            "github-actions-webhook:acme/yuki:12345",
            "github-actions-artifact:acme/yuki:12345",
            "github-actions-test-report:acme/yuki:12345",
        }
        evidence = TaskEvidenceStore(
            str(Path(self.tmp.name) / "artifact-evidence.sqlite3"),
            attestation_verifier=lambda item: item.verifier_id in accepted,
        )
        report = b"Ran 2 tests in 0.01s\\n\\nOK\\n"
        archive_buffer = io.BytesIO()
        with ZipFile(archive_buffer, "w") as zipped:
            zipped.writestr("yuki-test-results.log", report)
        archive = archive_buffer.getvalue()
        run = {
            "id": 77, "workflow_id": 12345, "conclusion": "success",
            "head_sha": "a" * 40, "head_branch": "main",
            "repository_full_name": "acme/yuki",
        }
        artifact = {
            "id": 88, "name": "yuki-test-report-77", "expired": False,
            "workflow_run_id": 77,
            "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
            "size_in_bytes": len(archive),
        }
        evidence.record_verified(EvidenceAttestation(
            owner_id="alice", task_id="task-1", task_version=3, kind="ci_workflow",
            reference="https://github.com/acme/yuki/actions/runs/77",
            digest_sha256="c" * 64,
            verifier_id="github-actions-webhook:acme/yuki:12345",
            verified_at="2026-11-23T00:00:00+00:00",
        ))
        verifier = GitHubActionsArtifactVerifier(
            evidence_store=evidence, allowed_repositories={"acme/yuki"},
            allowed_workflow_ids={12345}, owner_for_repository={"acme/yuki": "alice"},
            task_binding_resolver=lambda owner, task: (3, "a" * 40),
            workflow_run_lookup=lambda repo, run_id: run,
            artifact_lookup=lambda repo, run_id, name: artifact,
            artifact_download=lambda repo, artifact_id: archive,
        )
        self.tasks.get = lambda **kwargs: SimpleNamespace(
            task_id=kwargs["task_id"], owner_id=kwargs["actor_id"], state="running",
            version=3, updated_at="2026-11-23T00:00:00+00:00", dependencies=(),
        )
        self.api = AuthenticatedProjectAPI(
            self.app, _Auth(), self.registry, self.tasks, evidence,
            github_actions_artifact_verifier=verifier,
            github_actions_repository="acme/yuki",
        )
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Artifact proof"})
        project_id = created["project"]["project_id"]
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": "task-1"})
        status, ci_status = self.request(
            f"/v1/projects/{project_id}/tasks/task-1/ci-status",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ci_status["verification_state"], "workflow_verified")
        self.assertTrue(ci_status["workflow_verified"])
        self.assertFalse(ci_status["artifacts_verified"])
        self.assertFalse(ci_status["task_completed"])
        status, result = self.request(
            f"/v1/projects/{project_id}/tasks/task-1/verify-artifacts",
            method="POST", payload={"run_id": 77},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["verified"])
        self.assertEqual(result["test_count"], 2)
        status, ci_status = self.request(
            f"/v1/projects/{project_id}/tasks/task-1/ci-status",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ci_status["verification_state"], "artifacts_verified")
        self.assertTrue(ci_status["workflow_verified"])
        self.assertTrue(ci_status["artifacts_verified"])
        self.assertFalse(ci_status["task_completed"])
        kinds = {item.kind for item in evidence.list_verified(owner_id="alice", task_id="task-1", task_version=3)}
        self.assertEqual(kinds, {"ci_workflow", "artifact", "test_report"})

    def test_owner_authenticated_project_can_dispatch_exact_task_attempt_once(self):
        self.tasks.authorize = lambda actor, action, task: (
            actor == "alice" and action in {"task:create", "task:read", "task:ci:dispatch"}
        )
        self.tasks.get = lambda **kwargs: TaskRuntime.get(self.tasks, **kwargs)
        task = self.tasks.enqueue(
            actor_id="alice", idempotency_key="dispatch-api-task", name="dispatch-check",
            steps=[{"name": "review", "action": "emit", "argument": "inspect",
                    "requires_approval": False}],
        )
        calls = []
        dispatcher = GitHubActionsTaskDispatcher(
            repository="acme/yuki", workflow_id=12345, allowed_branches={"main"},
            task_runtime=self.tasks,
            ledger=GitHubActionsDispatchLedger(str(Path(self.tmp.name) / "dispatch-ledger.sqlite3")),
            source_binding_resolver=lambda owner, task_id: (task.version, "a" * 40, "main"),
            workflow_dispatch=lambda **kwargs: calls.append(kwargs),
        )
        self.api = AuthenticatedProjectAPI(
            self.app, _Auth(), self.registry, self.tasks,
            github_actions_dispatcher=dispatcher,
        )
        _, created = self.request("/v1/projects", method="POST", payload={"name": "CI dispatch"})
        project_id = created["project"]["project_id"]
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": task.task_id})
        path = f"/v1/projects/{project_id}/tasks/{task.task_id}/dispatch-ci"
        status, result = self.request(path, method="POST", payload={})
        self.assertEqual(status, 202)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["task_version"], task.version)
        self.assertEqual(result["source_sha"], "a" * 40)
        self.assertEqual(len(calls), 1)
        status, duplicate = self.request(path, method="POST", payload={})
        self.assertEqual(status, 409)
        self.assertEqual(duplicate["error"], "ci_dispatch_conflict")
        self.assertEqual(len(calls), 1)

    def test_owner_can_reconcile_uncertain_ci_dispatch_from_github_run(self):
        self.tasks.authorize = lambda actor, action, task: (
            actor == "alice" and action in {"task:create", "task:read", "task:ci:dispatch"}
        )
        self.tasks.get = lambda **kwargs: TaskRuntime.get(self.tasks, **kwargs)
        task = self.tasks.enqueue(
            actor_id="alice", idempotency_key="reconcile-api-task", name="reconcile-check",
            steps=[{"name": "review", "action": "emit", "argument": "inspect",
                    "requires_approval": False}],
        )
        ledger = GitHubActionsDispatchLedger(str(Path(self.tmp.name) / "reconcile-ledger.sqlite3"))
        dispatcher = GitHubActionsTaskDispatcher(
            repository="acme/yuki", workflow_id=12345, allowed_branches={"main"},
            task_runtime=self.tasks, ledger=ledger,
            source_binding_resolver=lambda owner, task_id: (task.version, "a" * 40, "main"),
            workflow_dispatch=lambda **kwargs: (_ for _ in ()).throw(TimeoutError("network timeout")),
            workflow_run_lookup=lambda **kwargs: {
                "id": 7654321, "workflow_id": 12345, "head_sha": "a" * 40,
                "repository_full_name": "acme/yuki",
            },
        )
        self.api = AuthenticatedProjectAPI(
            self.app, _Auth(), self.registry, self.tasks,
            github_actions_dispatcher=dispatcher,
        )
        _, created = self.request("/v1/projects", method="POST", payload={"name": "CI recovery"})
        project_id = created["project"]["project_id"]
        self.request(f"/v1/projects/{project_id}/tasks", method="POST", payload={"task_id": task.task_id})
        dispatch_path = f"/v1/projects/{project_id}/tasks/{task.task_id}/dispatch-ci"
        status, _ = self.request(dispatch_path, method="POST", payload={})
        self.assertEqual(status, 409)
        reconcile_path = f"/v1/projects/{project_id}/tasks/{task.task_id}/reconcile-ci"
        status, result = self.request(reconcile_path, method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertTrue(result["reconciled"])
        self.assertEqual(
            ledger.get_task_attempt(owner_id="alice", task_id=task.task_id, task_version=task.version)["state"],
            "dispatched",
        )
        status, repeated = self.request(reconcile_path, method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertFalse(repeated["reconciled"])

    def test_cross_owner_project_is_not_found(self):
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Yuki"})
        project_id = created["project"]["project_id"]
        status, _ = self.request(f"/v1/projects/{project_id}/tasks", token="bob-token")
        self.assertEqual(status, 404)

    def test_milestone_requires_evidence_and_supports_versioned_completion(self):
        _, created = self.request("/v1/projects", method="POST", payload={"name": "Yuki"})
        project_id = created["project"]["project_id"]
        status, result = self.request(f"/v1/projects/{project_id}/milestones", method="POST", payload={"title": "MVP"})
        self.assertEqual(status, 201)
        milestone_id = result["milestone"]["milestone_id"]
        status, completed = self.request(
            f"/v1/projects/milestones/{milestone_id}/complete", method="POST",
            payload={"expected_version": 1, "verification_ref": "ci:run-1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(completed["milestone"]["status"], "completed")
        self.assertEqual(completed["milestone"]["version"], 2)

    def test_authentication_and_input_validation(self):
        status, _ = self.request("/v1/projects", token=None)
        self.assertEqual(status, 401)
        status, _ = self.request("/v1/projects", method="POST", payload={"owner_id": "bob", "name": "x"})
        self.assertEqual(status, 400)

    def test_non_project_paths_delegate(self):
        self.request("/v1/auth/me")
        self.assertEqual(self.app.calls, 1)


if __name__ == "__main__":
    unittest.main()
