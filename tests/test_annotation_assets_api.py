import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.data_annotation import AuthenticatedAnnotationAPI
from core.annotation_assets import SQLiteAnnotationAssetStore
from core.auth import BuiltInAuthService
from core.data_annotation import SQLiteAnnotationWorkbench


class _Downstream:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


class AnnotationAssetAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner", "secure-owner-password")
        self.owner = self.auth.authenticate("owner", "secure-owner-password")
        self.auth.create_account("other", "secure-other-password")
        self.other = self.auth.authenticate("other", "secure-other-password")
        self.workbench = SQLiteAnnotationWorkbench(str(root / "workbench.sqlite"))
        self.assets = SQLiteAnnotationAssetStore(
            str(root / "assets"), str(root / "assets.sqlite")
        )
        self.app = AuthenticatedAnnotationAPI(
            _Downstream(), self.auth, self.workbench, self.assets,
            asset_scanner=lambda media_type, data: True,
        )
        self.project_id = self.workbench.create_project(
            self.owner.user.user_id, name="Image labels", task_type="image_classification",
            instructions="Choose a class", rubric={"labels": ["cat", "dog"]},
        ).project_id

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, body=b"", token=None, content_type=None):
        async def run():
            sent = []
            consumed = False
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
            if content_type:
                headers.append((b"content-type", content_type.encode()))
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": b"",
            }, receive, send)
            status = sent[0]["status"]
            payload = sent[1]["body"]
            return status, payload, dict(sent[0].get("headers", []))
        return asyncio.run(run())

    def test_upload_scan_preview_and_delete(self):
        path = f"/v1/annotation/projects/{self.project_id}/assets"
        data = b"\x89PNG\r\n\x1a\n" + b"pixel-data"
        status, body, _ = self.request(
            "POST", path, data, self.owner.token, "application/octet-stream"
        )
        self.assertEqual(status, 201)
        asset = json.loads(body)
        self.assertEqual(asset["scan_status"], "clean")
        asset_path = path + "/" + asset["asset_id"]
        status, preview, headers = self.request(
            "GET", asset_path, token=self.owner.token
        )
        self.assertEqual((status, preview), (200, data))
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertEqual(headers[b"content-disposition"], b"attachment")
        status, _, _ = self.request("DELETE", asset_path, token=self.owner.token)
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", asset_path, token=self.owner.token)
        self.assertEqual(status, 404)

    def test_owner_isolation_and_scanner_failure_stays_quarantined(self):
        self.app.asset_scanner = None
        path = f"/v1/annotation/projects/{self.project_id}/assets"
        data = b"GIF89a" + b"image"
        status, body, _ = self.request(
            "POST", path, data, self.owner.token, "application/octet-stream"
        )
        self.assertEqual(status, 202)
        asset = json.loads(body)
        self.assertEqual(asset["scan_status"], "quarantined")
        access = path + "/" + asset["asset_id"]
        status, _, _ = self.request("GET", access, token=self.owner.token)
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", access, token=self.other.token)
        self.assertEqual(status, 404)

    def test_task_binding_requires_clean_asset_in_same_owner_project(self):
        asset_path = f"/v1/annotation/projects/{self.project_id}/assets"
        data = b"\\x89PNG\\r\\n\\x1a\\n" + b"pixel-data"
        status, body, _ = self.request(
            "POST", asset_path, data, self.owner.token, "application/octet-stream"
        )
        self.assertEqual(status, 201)
        clean_asset_id = json.loads(body)["asset_id"]
        task_path = f"/v1/annotation/projects/{self.project_id}/tasks"
        task = {"external_ref": "clean-image", "input_data": {}, "asset_ref": clean_asset_id}
        status, body, _ = self.request(
            "POST", task_path, json.dumps(task).encode(), self.owner.token,
            "application/json",
        )
        self.assertEqual(status, 201, body)

        self.app.asset_scanner = None
        status, body, _ = self.request(
            "POST", asset_path, b"GIF89a" + b"image", self.owner.token,
            "application/octet-stream",
        )
        self.assertEqual(status, 202)
        quarantined_id = json.loads(body)["asset_id"]
        rejected = {"external_ref": "quarantined-image", "input_data": {},
                    "asset_ref": quarantined_id}
        status, body, _ = self.request(
            "POST", task_path, json.dumps(rejected).encode(), self.owner.token,
            "application/json",
        )
        self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_request"))

        foreign = {"external_ref": "foreign-image", "input_data": {},
                   "asset_ref": clean_asset_id}
        status, body, _ = self.request(
            "POST", task_path, json.dumps(foreign).encode(), self.other.token,
            "application/json",
        )
        self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_request"))

    def test_batch_task_binding_rejects_quarantined_asset_atomically(self):
        self.app.asset_scanner = None
        asset_path = f"/v1/annotation/projects/{self.project_id}/assets"
        status, body, _ = self.request(
            "POST", asset_path, b"GIF89a" + b"image", self.owner.token,
            "application/octet-stream",
        )
        self.assertEqual(status, 202)
        asset_id = json.loads(body)["asset_id"]
        task_path = f"/v1/annotation/projects/{self.project_id}/tasks/batch"
        batch = {"tasks": [
            {"external_ref": "valid-text-task", "input_data": {}},
            {"external_ref": "quarantined-image-task", "input_data": {},
             "asset_ref": asset_id},
        ]}
        status, body, _ = self.request(
            "POST", task_path, json.dumps(batch).encode(), self.owner.token,
            "application/json",
        )
        self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_request"))
        status, body, _ = self.request(
            "GET", f"/v1/annotation/projects/{self.project_id}/tasks",
            token=self.owner.token,
        )
        self.assertEqual(json.loads(body)["tasks"], [])

    def test_rejects_wrong_content_type_and_unknown_project(self):
        path = f"/v1/annotation/projects/{self.project_id}/assets"
        status, body, _ = self.request(
            "POST", path, b"GIF89a", self.owner.token, "image/gif"
        )
        self.assertEqual((status, json.loads(body)["error"]), (415, "application_octet_stream_required"))
        unknown = "f" * 32
        status, body, _ = self.request(
            "POST", f"/v1/annotation/projects/{unknown}/assets",
            b"GIF89a", self.owner.token, "application/octet-stream"
        )
        self.assertEqual((status, json.loads(body)["error"]), (404, "not_found"))


if __name__ == "__main__":
    unittest.main()
