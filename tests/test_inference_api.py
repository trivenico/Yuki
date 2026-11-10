import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.inference import AuthenticatedInferenceAPI
from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService


class _Downstream:
    async def __call__(self, scope, receive, send):
        body = b'{"delegated":true}'
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class AuthenticatedInferenceAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.owner_one = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.owner_two = self.auth.authenticate("owner-two", "another-secure-password")
        database = root / "inference.sqlite"
        self.pairing = DevicePairingService(database)
        self.jobs = InferenceJobService(database, self.pairing)
        self.app = AuthenticatedInferenceAPI(_Downstream(), self.auth, self.pairing, self.jobs)

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

    def _pair_device(self, session=None):
        status, challenge = self.request(
            "POST", "/v1/devices/pairing-challenges", {}, session or self.owner_one.token
        )
        self.assertEqual(status, 201)
        status, paired = self.request("POST", "/v1/devices/pair", {
            "code": challenge["code"], "name": "local-worker",
        })
        self.assertEqual(status, 201)
        return paired

    def test_owner_authentication_and_one_time_device_pairing(self):
        status, body = self.request("POST", "/v1/devices/pairing-challenges", {})
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        paired = self._pair_device()
        self.assertTrue(paired["device"]["device_id"].startswith("dev_"))
        self.assertNotIn("owner_id", paired["device"])
        status, body = self.request("POST", "/v1/devices/pair", {
            "code": "x" * 43, "name": "replay",
        })
        self.assertEqual((status, body["error"]), (401, "invalid_pairing_challenge"))

    def test_owner_scoped_job_lifecycle_and_separate_worker_credential(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "hello worker",
            "owner_id": self.owner_two.user.user_id,
        }, self.owner_one.token)
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "hello worker",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = body["job"]["job_id"]
        self.assertNotIn("input_text", body["job"])
        status, body = self.request("GET", f"/v1/inference/jobs/{job_id}", self.owner_two.token)
        self.assertEqual((status, body["error"]), (404, "not_found"))

        status, body = self.request("POST", "/v1/inference/worker/jobs/claim", {}, device_token)
        self.assertEqual(status, 200)
        claimed = body["job"]
        self.assertEqual(claimed["input_text"], "hello worker")
        self.assertTrue(claimed["lease_token"])
        self.assertNotIn("lease_token", claimed["public_dict"] if "public_dict" in claimed else {})

        status, body = self.request(
            "POST", f"/v1/inference/worker/jobs/{job_id}/complete",
            {"lease_token": claimed["lease_token"], "result_text": "finished"}, device_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["job"]["status"], "succeeded")
        status, body = self.request("GET", f"/v1/inference/jobs/{job_id}", token=self.owner_one.token)
        self.assertEqual((status, body["job"]["result_text"]), (200, "finished"))

    def test_revoked_device_cannot_claim_jobs_and_owner_cannot_revoke_another_device(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("DELETE", f"/v1/devices/{device_id}", token=self.owner_two.token)
        self.assertEqual((status, body["error"]), (404, "not_found"))
        status, body = self.request("DELETE", f"/v1/devices/{device_id}", token=self.owner_one.token)
        self.assertEqual((status, body["revoked"]), (200, True))
        status, body = self.request("POST", "/v1/inference/worker/jobs/claim", {}, device_token)
        self.assertEqual((status, body["error"]), (401, "invalid_device_credential"))

    def test_device_bearer_cannot_authenticate_owner_endpoints(self):
        paired = self._pair_device()
        status, body = self.request("GET", "/v1/devices", token=paired["device_token"])
        self.assertEqual((status, body["error"]), (401, "authentication_required"))

    def test_unrelated_routes_are_delegated(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


if __name__ == "__main__":
    unittest.main()
