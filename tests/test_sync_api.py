import io
import json
import tempfile
import unittest
from pathlib import Path

from core.sync_api import SyncAPI
from core.sync_store import SyncEventStore


def make_event():
    return {
        "event_id": "event_00000001", "device_id": "device_0000001",
        "collection": "task_records", "object_id": "task_00000001",
        "revision": 1, "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert", "payload": {"status": "open"},
    }


class SyncAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SyncEventStore(Path(self.tmp.name) / "hub.sqlite3")
        self.store.register_device("device_0000001", "user_0000001")
        self.api = SyncAPI(self.store, lambda token: "device_0000001" if token == "test-token" else "")

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, body=None, token="test-token", query=""):
        raw = b"" if body is None else json.dumps(body).encode()
        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(raw)) if body is not None else "",
            "wsgi.input": io.BytesIO(raw),
            "HTTP_AUTHORIZATION": f"Bearer {token}" if token is not None else "",
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)
        response = b"".join(self.api(environ, start_response))
        return captured, json.loads(response)

    def test_health_does_not_require_authentication(self):
        meta, payload = self.request("GET", "/health", token=None)
        self.assertEqual(meta["status"], "200 OK")
        self.assertTrue(payload["ok"])

    def test_rejects_missing_or_invalid_authentication(self):
        meta, _ = self.request("GET", "/v1/events", token=None)
        self.assertEqual(meta["status"], "401 Unauthorized")
        meta, _ = self.request("GET", "/v1/events", token="bad")
        self.assertEqual(meta["status"], "401 Unauthorized")

    def test_push_pull_and_ack_round_trip(self):
        meta, pushed = self.request("POST", "/v1/events", make_event())
        self.assertEqual(meta["status"], "201 Created")
        meta, pulled = self.request("GET", "/v1/events")
        self.assertEqual(meta["status"], "200 OK")
        self.assertEqual(pulled["events"][0]["event_id"], "event_00000001")
        meta, ack = self.request("POST", "/v1/ack", {"sequence": pushed["sequence"]})
        self.assertEqual(meta["status"], "200 OK")
        self.assertEqual(ack["acknowledged"], 1)

    def test_conflicts_return_409(self):
        self.request("POST", "/v1/events", make_event())
        raw = make_event()
        raw["event_id"] = "event_00000002"
        raw["revision"] = 3
        meta, _ = self.request("POST", "/v1/events", raw)
        self.assertEqual(meta["status"], "409 Conflict")

    def test_rejects_oversized_request(self):
        raw = make_event()
        raw["payload"] = {"description": "x" * (256 * 1024)}
        meta, _ = self.request("POST", "/v1/events", raw)
        self.assertEqual(meta["status"], "400 Bad Request")

    def test_unknown_routes_do_not_expose_data(self):
        meta, payload = self.request("GET", "/admin/devices")
        self.assertEqual(meta["status"], "404 Not Found")
        self.assertEqual(payload, {"error": "not_found"})


if __name__ == "__main__":
    unittest.main()
