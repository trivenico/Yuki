import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.projects import AuthenticatedProjectAPI
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.project_registry import ProjectRegistry
from core.task_evidence import EvidenceAttestation, TaskEvidenceStore


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
