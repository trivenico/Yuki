import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from cloud.device_registry import DeviceCredentialStore
from cloud.gateway_server import MAX_WORKERS, create_server


class GatewayHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = str(Path(self.tmp.name) / "gateway.sqlite3")
        self.admin = "test-admin-token-with-more-than-32-characters"
        self.server = create_server(
            database_path=self.database,
            admin_token=self.admin,
            host="127.0.0.1",
            port=0,
            bootstrap_enabled=True,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.host, self.port = self.server.server_address

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, token=None):
        connection = HTTPConnection(self.host, self.port, timeout=2)
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        status = response.status
        cache = response.getheader("Cache-Control")
        connection.close()
        return status, data, cache

    def test_health_endpoint_is_minimal_and_non_cacheable(self):
        status, data, cache = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(cache, "no-store")

    def test_admin_only_enrollment_and_device_authentication(self):
        status, _, _ = self.request(
            "POST", "/v1/admin/devices",
            {"user_id": "user-1", "device_name": "Yuki Desktop"},
        )
        self.assertEqual(status, 401)

        status, issued, _ = self.request(
            "POST", "/v1/admin/devices",
            {"user_id": "user-1", "device_name": "Yuki Desktop"},
            token=self.admin,
        )
        self.assertEqual(status, 201)
        self.assertTrue(issued["token"].startswith("yuki_dev_"))

        status, identity, _ = self.request("GET", "/v1/me", token=issued["token"])
        self.assertEqual(status, 200)
        self.assertEqual(identity["user_id"], "user-1")
        self.assertEqual(identity["device_id"], issued["device_id"])

    def test_device_list_requires_scope_and_is_user_scoped(self):
        store = DeviceCredentialStore(self.database)
        scoped = store.issue(
            user_id="user-1", device_name="Admin view",
            scopes=("profile:read", "devices:read"),
        )
        status, data, _ = self.request("GET", "/v1/devices", token=scoped.token)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["devices"]), 1)
        self.assertNotIn("token", data["devices"][0])

        limited = store.issue(
            user_id="user-1", device_name="Limited",
            scopes=("profile:read",),
        )
        status, data, _ = self.request("GET", "/v1/devices", token=limited.token)
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "insufficient_scope")

    def test_admin_revocation_invalidates_device_token(self):
        status, issued, _ = self.request(
            "POST", "/v1/admin/devices",
            {"user_id": "user-1", "device_name": "Yuki CLI"},
            token=self.admin,
        )
        self.assertEqual(status, 201)
        status, data, _ = self.request(
            "DELETE", "/v1/admin/devices/" + issued["device_id"],
            {"user_id": "user-1"}, token=self.admin,
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["revoked"])
        status, _, _ = self.request("GET", "/v1/me", token=issued["token"])
        self.assertEqual(status, 401)

    def test_bootstrap_is_disabled_by_default(self):
        disabled = create_server(
            database_path=str(Path(self.tmp.name) / "disabled.sqlite3"),
            admin_token=self.admin,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=disabled.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = disabled.server_address
            connection = HTTPConnection(host, port, timeout=2)
            connection.request(
                "POST", "/v1/admin/devices",
                body=json.dumps({"user_id": "user-1", "device_name": "Laptop"}),
                headers={"Authorization": "Bearer " + self.admin, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            connection.close()
        finally:
            disabled.shutdown()
            disabled.server_close()
            thread.join(timeout=2)

    def test_server_returns_retryable_overload_when_worker_slots_are_full(self):
        acquired = 0
        try:
            for _ in range(MAX_WORKERS):
                self.assertTrue(self.server._request_slots.acquire(blocking=False))
                acquired += 1
            connection = HTTPConnection(self.host, self.port, timeout=2)
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(response.getheader("Retry-After"), "1")
            self.assertEqual(response.read(), b"")
            connection.close()
        finally:
            for _ in range(acquired):
                self.server._request_slots.release()

    def test_server_rejects_unbounded_worker_configuration(self):
        with self.assertRaises(ValueError):
            create_server(
                database_path=str(Path(self.tmp.name) / "invalid.sqlite3"),
                admin_token=self.admin,
                max_workers=0,
            )

    def test_invalid_admin_secret_is_rejected(self):
        status, _, _ = self.request(
            "POST", "/v1/admin/devices",
            {"user_id": "user-1", "device_name": "Laptop"},
            token="wrong",
        )
        self.assertEqual(status, 401)

    def test_unknown_routes_are_not_exposed(self):
        status, data, _ = self.request("GET", "/v1/tasks")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
