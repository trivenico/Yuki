import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.data_annotation import AuthenticatedAnnotationAPI
from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.data_annotation import SQLiteAnnotationWorkbench
from core.annotation_assets import SQLiteAnnotationAssetStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class _Downstream:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"delegated":true}'})


class AuthenticatedAnnotationAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.owner_one = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.owner_two = self.auth.authenticate("owner-two", "another-secure-password")
        self.store = SQLiteAnnotationWorkbench(str(root / "annotation.sqlite"))
        self.app = AuthenticatedAnnotationAPI(_Downstream(), self.auth, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed = False
            sent = []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": b"",
            }, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())

    def create_project(self, ai_assistance_mode="permitted_with_disclosure"):
        status, body = self.request(
            "POST", "/v1/annotation/projects",
            {
                "name": "Text sentiment",
                "task_type": "text_classification",
                "instructions": "Choose positive, neutral or negative.",
                "rubric": {"labels": ["positive", "neutral", "negative"]},
                "ai_assistance_mode": ai_assistance_mode,
            }, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        return body["project"]["project_id"]

    def grant_reviewer(self, project_id):
        status, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/reviewers",
            {"reviewer_user_id": self.owner_two.user.user_id}, self.owner_one.token,
        )
        self.assertEqual((status, body["granted"]), (201, True))

    def test_authentication_and_owner_id_spoofing_are_rejected(self):
        status, body = self.request("GET", "/v1/annotation/projects")
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        payload = {
            "name": "spoofed", "task_type": "custom",
            "instructions": "test", "rubric": {}, "owner_id": "owner-two",
        }
        status, body = self.request(
            "POST", "/v1/annotation/projects", payload, self.owner_one.token
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))

    def test_authenticated_project_task_claim_submit_and_review(self):
        project_id = self.create_project()
        status, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "task-1", "input_data": {"text": "Great service"}},
            self.owner_one.token,
        )
        self.assertEqual(status, 201)
        task = body["task"]
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        self.assertEqual(status, 200)
        claimed = body["task"]
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {
                "payload": {"label": "positive"},
                "expected_version": claimed["version"],
                "ai_assisted": True,
                "human_confirmed": True,
            }, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        submission_id = body["submission"]["submission_id"]
        self.grant_reviewer(project_id)
        status, body = self.request(
            "POST", f"/v1/annotation/submissions/{submission_id}/review",
            {"decision": "accepted", "notes": "Matches rubric.", "project_id": project_id},
            self.owner_two.token,
        )
        self.assertEqual((status, body["submission"]["status"]), (200, "accepted"))

    def test_reviewer_queue_and_decisions_require_separate_authorized_identity(self):
        project_id = self.create_project()
        _, created = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "review-separation", "input_data": {"text": "Review me"}},
            self.owner_one.token,
        )
        self.grant_reviewer(project_id)
        task = created["task"]
        _, claimed = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        _, submitted = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {"payload": {"label": "positive"},
             "expected_version": claimed["task"]["version"], "human_confirmed": True},
            self.owner_one.token,
        )
        submission_id = submitted["submission"]["submission_id"]
        self.grant_reviewer(project_id)
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/review-queue",
            token=self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (403, "reviewer_access_denied"))
        status, body = self.request(
            "POST", f"/v1/annotation/submissions/{submission_id}/review",
            {"decision": "accepted", "project_id": project_id}, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (403, "reviewer_access_denied"))
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/review-queue",
            token=self.owner_two.token,
        )
        self.assertEqual((status, len(body["submissions"])), (200, 1))
        status, body = self.request(
            "POST", f"/v1/annotation/submissions/{submission_id}/review",
            {"decision": "accepted", "project_id": project_id}, self.owner_two.token,
        )
        self.assertEqual((status, body["submission"]["reviewer_id"]), (200, self.owner_two.user.user_id))

    def test_reviewer_authorization_cannot_be_reused_for_another_project(self):
        project_id = self.create_project()
        other_project = self.create_project()
        _, created = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "bound-review", "input_data": {"text": "Bound"}},
            self.owner_one.token,
        )
        task = created["task"]
        _, claimed = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        _, submitted = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {"payload": {"label": "positive"},
             "expected_version": claimed["task"]["version"], "human_confirmed": True},
            self.owner_one.token,
        )
        self.grant_reviewer(project_id)
        self.grant_reviewer(other_project)
        status, body = self.request(
            "POST", f"/v1/annotation/submissions/{submitted['submission']['submission_id']}/review",
            {"decision": "accepted", "project_id": other_project}, self.owner_two.token,
        )
        self.assertEqual((status, body["error"]), (404, "not_found"))
        self.assertEqual(
            self.store.get_task(self.owner_one.user.user_id, task["task_id"]).status, "submitted"
        )

    def test_quality_report_api_is_owner_scoped_and_returns_aggregates(self):
        status, created = self.request(
            "POST", "/v1/annotation/projects", {
                "name": "Quality project", "task_type": "text_classification",
                "instructions": "Classify the sentence.",
                "rubric": {"labels": ["positive", "negative"], "quality": {
                    "minimum_confidence": 0.8, "require_rationale": True,
                }},
            }, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        project_id = created["project"]["project_id"]
        status, created_task = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "quality-001", "input_data": {"text": "Great work."}},
            self.owner_one.token,
        )
        self.assertEqual(status, 201)
        task = created_task["task"]
        status, claimed = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        self.assertEqual(status, 200)
        status, submitted = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {"payload": {"label": "positive", "confidence": 0.3},
             "expected_version": claimed["task"]["version"], "human_confirmed": True},
            self.owner_one.token,
        )
        self.assertEqual(status, 201)
        self.assertEqual(submitted["submission"]["quality"]["status"], "warning")
        status, report = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/quality-report",
            token=self.owner_one.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["report"]["finding_counts"], {
            "confidence_below_threshold": 1, "rationale_missing": 1,
        })
        self.assertEqual(report["report"]["quality"]["warning"], 1)
        status, denied = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/quality-report",
            token=self.owner_two.token,
        )
        self.assertEqual((status, denied["error"]), (404, "not_found"))

    def test_owner_can_grant_list_and_revoke_reviewer_access(self):
        project_id = self.create_project()
        self.grant_reviewer(project_id)
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/reviewers",
            token=self.owner_one.token,
        )
        self.assertEqual((status, body["reviewers"][0]["reviewer_id"]),
                         (200, self.owner_two.user.user_id))
        status, body = self.request(
            "DELETE", f"/v1/annotation/projects/{project_id}/reviewers",
            {"reviewer_user_id": self.owner_two.user.user_id}, self.owner_one.token,
        )
        self.assertEqual((status, body["revoked"]), (200, True))
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/review-queue",
            token=self.owner_two.token,
        )
        self.assertEqual((status, body["error"]), (403, "reviewer_access_denied"))

    def test_reviewer_routes_fail_closed_without_active_grant(self):
        project_id = self.create_project()
        self.app = AuthenticatedAnnotationAPI(_Downstream(), self.auth, self.store)
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/review-queue",
            token=self.owner_two.token,
        )
        self.assertEqual((status, body["error"]), (403, "reviewer_access_denied"))

    def test_other_owner_cannot_read_or_submit_project_tasks(self):
        project_id = self.create_project()
        status, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "task-2", "input_data": {"text": "Private"}},
            self.owner_one.token,
        )
        task_id = body["task"]["task_id"]
        status, body = self.request(
            "GET", f"/v1/annotation/projects/{project_id}", token=self.owner_two.token
        )
        self.assertEqual((status, body["error"]), (404, "not_found"))
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task_id}/claim",
            {"expected_version": 1}, self.owner_two.token,
        )
        self.assertEqual((status, body["error"]), (404, "not_found"))

    def test_batch_import_and_accepted_only_export_routes(self):
        project_id = self.create_project()
        status, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks/batch",
            {"tasks": [
                {"external_ref": "batch-1", "input_data": {"text": "good"}},
                {"external_ref": "batch-2", "input_data": {"text": "bad"}},
            ]}, self.owner_one.token,
        )
        self.assertEqual((status, len(body["tasks"])), (201, 2))
        self.assertEqual(
            self.request("GET", f"/v1/annotation/projects/{project_id}/export",
                         token=self.owner_one.token)[1]["accepted_annotations"], []
        )
        task = body["tasks"][0]
        _, claimed_body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        _, submitted_body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {"payload": {"label": "positive"},
             "expected_version": claimed_body["task"]["version"],
             "human_confirmed": True}, self.owner_one.token,
        )
        submission_id = submitted_body["submission"]["submission_id"]
        self.grant_reviewer(project_id)
        self.request(
            "POST", f"/v1/annotation/submissions/{submission_id}/review",
            {"decision": "accepted", "project_id": project_id}, self.owner_two.token,
        )
        status, exported = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/export",
            token=self.owner_one.token,
        )
        self.assertEqual((status, len(exported["accepted_annotations"])), (200, 1))

    def test_project_defaults_to_human_only_ai_policy(self):
        project_id = self.create_project(ai_assistance_mode="human_only")
        _, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "human-only", "input_data": {"text": "Sample"}},
            self.owner_one.token,
        )
        task = body["task"]
        _, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {"payload": {"label": "neutral"},
             "expected_version": body["task"]["version"],
             "ai_assisted": True, "human_confirmed": True},
            self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (403, "ai_assistance_not_permitted"))

    def test_human_confirmation_required_and_version_conflict_mapped(self):
        project_id = self.create_project()
        _, body = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "task-3", "input_data": {"text": "Sample"}},
            self.owner_one.token,
        )
        task = body["task"]
        _, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/claim",
            {"expected_version": 1}, self.owner_one.token,
        )
        claimed = body["task"]
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {
                "payload": {"label": "neutral"},
                "expected_version": claimed["version"],
                "human_confirmed": False,
            }, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (403, "human_confirmation_required"))
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task['task_id']}/submit",
            {
                "payload": {"label": "neutral"},
                "expected_version": 1,
                "human_confirmed": True,
            }, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (409, "version_conflict"))

    def test_typed_payload_validation_rejects_invalid_label_without_advancing_task(self):
        project_id = self.create_project()
        _, created = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "typed-1", "input_data": {"text": "A plane"}},
            self.owner_one.token,
        )
        task = created["task"]
        task_id = task["task_id"]
        _, claimed = self.request(
            "POST", f"/v1/annotation/tasks/{task_id}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        status, body = self.request(
            "POST", f"/v1/annotation/tasks/{task_id}/submit",
            {"payload": {"label": "airplane"},
             "expected_version": claimed["task"]["version"],
             "human_confirmed": True},
            self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        _, tasks = self.request(
            "GET", f"/v1/annotation/projects/{project_id}/tasks",
            token=self.owner_one.token,
        )
        unchanged = next(item for item in tasks["tasks"] if item["task_id"] == task_id)
        self.assertEqual((unchanged["status"], unchanged["version"]), ("in_progress", claimed["task"]["version"]))
        _, submissions = self.request(
            "GET", f"/v1/annotation/tasks/{task_id}/submissions",
            token=self.owner_one.token,
        )
        self.assertEqual(submissions["submissions"], [])

    def test_draft_api_is_authenticated_owner_scoped_and_revision_checked(self):
        project_id = self.create_project()
        _, created = self.request(
            "POST", f"/v1/annotation/projects/{project_id}/tasks",
            {"external_ref": "draft-1", "input_data": {"text": "A bird"}},
            self.owner_one.token,
        )
        task = created["task"]
        task_id = task["task_id"]
        self.request(
            "POST", f"/v1/annotation/tasks/{task_id}/claim",
            {"expected_version": task["version"]}, self.owner_one.token,
        )
        status, body = self.request(
            "GET", f"/v1/annotation/tasks/{task_id}/draft",
            token=self.owner_one.token,
        )
        self.assertEqual((status, body["draft"]), (200, None))
        status, body = self.request(
            "PUT", f"/v1/annotation/tasks/{task_id}/draft",
            {"payload": {"label": "in progress"}, "expected_revision": 0},
            self.owner_one.token,
        )
        self.assertEqual((status, body["draft"]["revision"]), (200, 1))
        status, body = self.request(
            "GET", f"/v1/annotation/tasks/{task_id}/draft",
            token=self.owner_two.token,
        )
        self.assertEqual((status, body["draft"]), (200, None))
        status, body = self.request(
            "PUT", f"/v1/annotation/tasks/{task_id}/draft",
            {"payload": {"label": "stale"}, "expected_revision": 0},
            self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (409, "version_conflict"))
        status, body = self.request(
            "DELETE", f"/v1/annotation/tasks/{task_id}/draft",
            {"expected_revision": 1}, self.owner_one.token,
        )
        self.assertEqual((status, body["deleted"]), (200, True))

    def test_unrelated_routes_are_delegated(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


class AnnotationRuntimeTests(unittest.TestCase):
    def test_runtime_mounts_annotation_api_when_configured(self):
        def model(**kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            store = SQLiteAnnotationWorkbench(str(root / "annotation.sqlite"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=loop, annotation_workbench=store,
                annotation_reviewer_authorizer=lambda user, project_id: None,
            )
            self.assertIsInstance(runtime.app, AuthenticatedAnnotationAPI)
            self.assertIs(runtime.app.workbench, store)
            self.assertTrue(callable(runtime.app.reviewer_authorizer))
            runtime.close()

    def test_runtime_mounts_asset_store_only_with_annotation_workbench(self):
        def model(**kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            workbench = SQLiteAnnotationWorkbench(str(root / "annotation.sqlite"))
            assets = SQLiteAnnotationAssetStore(str(root / "assets"), str(root / "assets.sqlite"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=loop,
                annotation_workbench=workbench, annotation_assets=assets,
                annotation_asset_scanner=lambda media_type, data: True,
            )
            self.assertIs(runtime.app.asset_store, assets)
            self.assertTrue(callable(runtime.app.asset_scanner))
            runtime.close()

    def test_runtime_rejects_invalid_annotation_store(self):
        def model(**kwargs):
            return {"text": "ready", "tool_calls": {"tool_calls": []}}
        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        )
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            with self.assertRaisesRegex(TypeError, "annotation_workbench"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=loop, annotation_workbench=object(),
                )


if __name__ == "__main__":
    unittest.main()
