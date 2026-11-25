import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core.audit_anchor_service import (
    AuditAnchorASGI,
    AuditAnchorConflict,
    AuditAnchorUnauthorized,
    SQLiteAuditAnchorService,
)
from core.authorization_audit import AuthorizationAuditCheckpoint


class SQLiteAuditAnchorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "anchor.sqlite3")
        self.service = SQLiteAuditAnchorService(
            self.database,
            authorize_administrator=lambda identity: identity == "anchor-admin",
        )
        self.service.set_publisher("anchor-admin", "yuki-prod-publisher", enabled=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_checkpoint_requires_create_only_and_server_stamps_time(self):
        self.assertIsNone(self.service.get_checkpoint("yuki-prod-publisher"))
        with self.assertRaises(AuditAnchorConflict):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(0, "0" * 64),
                if_match=None,
                if_none_match=None,
            )
        stored = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(0, "0" * 64),
            if_match=None,
            if_none_match="*",
        )
        self.assertEqual(stored.checkpoint.event_id, 0)
        self.assertTrue(stored.checkpoint.published_at.endswith("+00:00"))
        self.assertTrue(stored.etag.startswith('"') and stored.etag.endswith('"'))

    def test_compare_and_set_and_monotonic_event_history(self):
        first = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(2, "a" * 64),
            if_match=None,
            if_none_match="*",
        )
        second = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(7, "b" * 64),
            if_match=first.etag,
            if_none_match=None,
        )
        self.assertEqual(second.checkpoint.event_id, 7)
        with self.assertRaisesRegex(AuditAnchorConflict, "compare_and_set"):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(9, "c" * 64),
                if_match=first.etag,
                if_none_match=None,
            )
        with self.assertRaisesRegex(AuditAnchorConflict, "event_id_regression"):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(6, "d" * 64),
                if_match=second.etag,
                if_none_match=None,
            )
        with self.assertRaisesRegex(AuditAnchorConflict, "same_event_hash"):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(7, "e" * 64),
                if_match=second.etag,
                if_none_match=None,
            )
        with self.assertRaisesRegex(AuditAnchorConflict, "hash_did_not_advance"):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(8, "b" * 64),
                if_match=second.etag,
                if_none_match=None,
            )

    def test_concurrent_publishers_cannot_both_advance_same_etag(self):
        first = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(1, "a" * 64),
            if_match=None,
            if_none_match="*",
        )
        barrier = threading.Barrier(2)

        def advance(event_id, event_hash):
            barrier.wait(timeout=5)
            try:
                stored = self.service.publish_checkpoint(
                    "yuki-prod-publisher",
                    AuthorizationAuditCheckpoint(event_id, event_hash),
                    if_match=first.etag,
                    if_none_match=None,
                )
                return ("published", stored.checkpoint.event_id)
            except AuditAnchorConflict:
                return ("conflict", event_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda item: advance(*item),
                ((2, "b" * 64), (3, "c" * 64)),
            ))

        self.assertEqual(sum(result[0] == "published" for result in results), 1)
        self.assertEqual(sum(result[0] == "conflict" for result in results), 1)
        latest = self.service.get_checkpoint("yuki-prod-publisher")
        self.assertIn(latest.checkpoint.event_id, (2, 3))
        self.assertEqual(latest.checkpoint.event_id, next(
            value for status, value in results if status == "published"
        ))

    def test_identical_checkpoint_is_idempotent(self):
        first = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(5, "f" * 64),
            if_match=None,
            if_none_match="*",
        )
        repeated = self.service.publish_checkpoint(
            "yuki-prod-publisher",
            AuthorizationAuditCheckpoint(5, "f" * 64),
            if_match=first.etag,
            if_none_match=None,
        )
        self.assertEqual(repeated, first)

    def test_revoked_publisher_cannot_read_or_publish(self):
        self.service.set_publisher("anchor-admin", "yuki-prod-publisher", enabled=False)
        with self.assertRaises(AuditAnchorUnauthorized):
            self.service.get_checkpoint("yuki-prod-publisher")
        with self.assertRaises(AuditAnchorUnauthorized):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(0, "0" * 64),
                if_match=None,
                if_none_match="*",
            )
        audit = self.service.administrative_audit("anchor-admin")
        self.assertEqual([item["operation"] for item in audit], ["enable", "revoke"])

    def test_untrusted_administrator_cannot_enroll_or_read_admin_audit(self):
        with self.assertRaises(AuditAnchorUnauthorized):
            self.service.set_publisher("attacker", "attacker-publisher", enabled=True)
        with self.assertRaises(AuditAnchorUnauthorized):
            self.service.administrative_audit("attacker")

    def test_checkpoint_payload_validation_rejects_bool_event_id(self):
        with self.assertRaisesRegex(ValueError, "checkpoint_invalid"):
            self.service.publish_checkpoint(
                "yuki-prod-publisher",
                AuthorizationAuditCheckpoint(True, "0" * 64),
                if_match=None,
                if_none_match="*",
            )


class AuditAnchorASGITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = SQLiteAuditAnchorService(
            str(Path(self.temp_dir.name) / "anchor.sqlite3"),
            authorize_administrator=lambda identity: identity == "admin",
        )
        self.service.set_publisher("admin", "publisher", enabled=True)
        self.app = AuditAnchorASGI(
            self.service,
            authenticate_scope=lambda scope: scope.get("trusted_publisher"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _request(self, method, *, body=b"", headers=(), identity="publisher"):
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []
        async def receive():
            return messages.pop(0)
        async def send(message):
            sent.append(message)
        scope = {
            "type": "http",
            "method": method,
            "path": "/v1/checkpoint",
            "headers": list(headers),
            "trusted_publisher": identity,
        }
        asyncio.run(self.app(scope, receive, send))
        return sent[0], sent[1]

    def test_get_uninitialized_and_put_checkpoint(self):
        start, _ = self._request("GET")
        self.assertEqual(start["status"], 404)
        body = json.dumps({"event_id": 0, "event_hash": "0" * 64}).encode()
        start, response = self._request(
            "PUT", body=body, headers=((b"if-none-match", b"*"),)
        )
        self.assertEqual(start["status"], 200)
        etags = [value for key, value in start["headers"] if key == b"etag"]
        self.assertEqual(len(etags), 1)
        payload = json.loads(response["body"])
        self.assertEqual(payload["event_id"], 0)
        self.assertTrue(payload["published_at"])

    def test_authentication_failure_and_duplicate_conditionals_fail_closed(self):
        start, _ = self._request("GET", identity=None)
        self.assertEqual(start["status"], 401)
        body = json.dumps({"event_id": 0, "event_hash": "0" * 64}).encode()
        start, _ = self._request(
            "PUT", body=body,
            headers=((b"if-none-match", b"*"), (b"if-none-match", b"*")),
        )
        self.assertEqual(start["status"], 400)

    def test_duplicate_json_members_rejected(self):
        body = b'{"event_id":0,"event_id":1,"event_hash":"' + b"0" * 64 + b'"}'
        start, _ = self._request(
            "PUT", body=body, headers=((b"if-none-match", b"*"),)
        )
        self.assertEqual(start["status"], 400)


if __name__ == "__main__":
    unittest.main()
