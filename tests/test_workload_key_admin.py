import asyncio
import base64
import json
import unittest

from core.workload_key_admin import WorkloadKeyAdminASGI
from core.workload_key_registry import SQLiteWorkloadKeyRegistry


class RegistryInterfaceFake:
    """Structural stand-in for a registry hosted by a remote database."""

    def __init__(self):
        self.keys = {}
        self.events = []

    def register(self, administrator, key):
        self.keys[(key.issuer, key.key_id)] = ("staged", key)
        self.events.append({"action": "register", "issuer": key.issuer, "key_id": key.key_id})

    def activate(self, administrator, issuer, key_id):
        state, key = self.keys[(issuer, key_id)]
        self.keys[(issuer, key_id)] = ("active", key)
        self.events.append({"action": "active", "issuer": issuer, "key_id": key_id})

    def retire(self, administrator, issuer, key_id):
        state, key = self.keys[(issuer, key_id)]
        self.keys[(issuer, key_id)] = ("retired", key)

    def revoke(self, administrator, issuer, key_id):
        state, key = self.keys[(issuer, key_id)]
        self.keys[(issuer, key_id)] = ("revoked", key)

    def audit_events(self, administrator):
        return tuple(self.events)


class WorkloadKeyAdminRegistryInterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, method, path, payload=None):
        raw = b"" if payload is None else json.dumps(payload).encode()
        sent = []
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message):
            sent.append(message)

        await app({"type": "http", "path": path, "method": method}, receive, send)
        return sent[0]["status"], sent[-1].get("body", b"")

    async def test_accepts_postgres_compatible_registry_interface(self):
        registry = RegistryInterfaceFake()
        app = WorkloadKeyAdminASGI(registry, lambda scope: "security-admin")
        public_key = b"p" * 32
        status, body = await self.request(
            app, "PUT", "/v1/admin/keys/yuki-prod/key-1",
            {"algorithm": "EdDSA", "public_key": base64.b64encode(public_key).decode()},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body), {"status": "staged"})
        status, _ = await self.request(
            app, "POST", "/v1/admin/keys/yuki-prod/key-1/activate",
        )
        self.assertEqual(status, 204)
        self.assertEqual(registry.keys[("yuki-prod", "key-1")][0], "active")

    async def test_accepts_existing_sqlite_registry(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            registry = SQLiteWorkloadKeyRegistry(
                str(Path(directory) / "keys.sqlite"),
                authorize_admin=lambda value: value == "security-admin",
            )
            app = WorkloadKeyAdminASGI(registry, lambda scope: "security-admin")
            status, body = await self.request(app, "GET", "/v1/admin/keys/audit")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"events": []})

    def test_rejects_object_without_registry_contract(self):
        with self.assertRaisesRegex(TypeError, "workload_key_registry_interface_required"):
            WorkloadKeyAdminASGI(object(), lambda scope: "security-admin")


if __name__ == "__main__":
    unittest.main()
