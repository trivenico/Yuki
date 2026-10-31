import io
import json
import tempfile
import unittest
from pathlib import Path

from core.sync_api import SyncAPI
from core.sync_auth import SyncCredentialStore
from core.sync_store import SyncEventStore


class SyncCredentialAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SyncEventStore(Path(self.tmp.name) / "sync.sqlite3")
        self.credentials = SyncCredentialStore(self.store)
        self.api = SyncAPI(self.store, credentials=self.credentials)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=None):
        raw = b"" if body is None else json.dumps(body).encode()
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)) if body is not None else "",
            "wsgi.input": io.BytesIO(raw),
            "HTTP_AUTHORIZATION": "Bearer " + token if token else "",
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)
        payload = b"".join(self.api(environ, start_response))
        return captured, json.loads(payload)

    def test_pairing_endpoint_consumes_code_and_returns_credentials(self):
        code = self.credentials.create_pairing_code("user_0000001")
        meta, pair = self.request("POST", "/v1/pair", {"code": code, "device_label": "Windows"})
        self.assertEqual(meta["status"], "201 Created")
        self.assertEqual(self.credentials.authenticate_access(pair["access_token"]), pair["device_id"])
        meta, _ = self.request("POST", "/v1/pair", {"code": code})
        self.assertEqual(meta["status"], "401 Unauthorized")

    def test_refresh_endpoint_rotates_access_and_refresh_tokens(self):
        code = self.credentials.create_pairing_code("user_0000001")
        _, pair = self.request("POST", "/v1/pair", {"code": code})
        meta, rotated = self.request("POST", "/v1/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(meta["status"], "200 OK")
        self.assertNotEqual(rotated["access_token"], pair["access_token"])
        meta, _ = self.request("GET", "/v1/events", token=pair["access_token"])
        self.assertEqual(meta["status"], "401 Unauthorized")
        meta, _ = self.request("GET", "/v1/events", token=rotated["access_token"])
        self.assertEqual(meta["status"], "200 OK")

    def test_sync_endpoints_authenticate_against_credential_store(self):
        code = self.credentials.create_pairing_code("user_0000001")
        _, pair = self.request("POST", "/v1/pair", {"code": code})
        event = {
            "event_id": "event_00000001", "device_id": pair["device_id"],
            "collection": "task_records", "object_id": "task_00000001",
            "revision": 1, "occurred_at": "2026-10-31T10:00:00Z",
            "operation": "upsert", "payload": {"status": "open"},
        }
        meta, pushed = self.request("POST", "/v1/events", event, pair["access_token"])
        self.assertEqual(meta["status"], "201 Created")
        meta, pulled = self.request("GET", "/v1/events", token=pair["access_token"])
        self.assertEqual(meta["status"], "200 OK")
        self.assertEqual(pulled["events"][0]["sequence"], pushed["sequence"])

    def test_refresh_token_replay_revokes_device(self):
        code = self.credentials.create_pairing_code("user_0000001")
        _, pair = self.request("POST", "/v1/pair", {"code": code})
        meta, _ = self.request("POST", "/v1/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(meta["status"], "200 OK")
        meta, _ = self.request("POST", "/v1/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(meta["status"], "401 Unauthorized")
        meta, _ = self.request("GET", "/v1/events", token=pair["access_token"])
        self.assertEqual(meta["status"], "401 Unauthorized")


if __name__ == "__main__":
    unittest.main()
