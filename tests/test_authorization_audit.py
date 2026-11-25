import sqlite3
import json
import ssl
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_audit import AuthorizationAuditCheckpoint, AuthorizationAuditCheckpointPublisher, HttpsAuthorizationAuditAnchor, SQLiteAuthorizationAuditLog


class SQLiteAuthorizationAuditLogTests(unittest.TestCase):
    def test_gateway_events_persist_and_chain_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            first = SQLiteAuthorizationAuditLog(path)
            request = AuthorizationRequest(
                Principal("alice", "human", tenant_id="tenant-1"),
                "tool:invoke", "tool:private-resource", risk="high",
                task_id="task-1", expected_policy_revision="revision-7",
            )
            decision = AuthorizationDecision(True, "decision-secret", "revision-7", "allowed")
            self.assertEqual(first(request, decision), 1)
            second = SQLiteAuthorizationAuditLog(path)
            self.assertEqual(second.head_hash(), first.head_hash())
            self.assertTrue(second.verify_chain())
            with sqlite3.connect(path) as db:
                row = db.execute(
                    "SELECT principal_hash, resource_hash, decision_hash, allowed FROM authorization_audit_events"
                ).fetchone()
            self.assertEqual(row[0], SQLiteAuthorizationAuditLog._digest("alice"))
            self.assertEqual(row[1], SQLiteAuthorizationAuditLog._digest("tool:private-resource"))
            self.assertEqual(row[2], SQLiteAuthorizationAuditLog._digest("decision-secret"))
            self.assertEqual(row[3], 1)
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertNotIn(b"alice", raw)
            self.assertNotIn(b"tool:private-resource", raw)
            self.assertNotIn(b"decision-secret", raw)

    def test_chain_detects_row_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            audit = SQLiteAuthorizationAuditLog(path)
            audit(
                AuthorizationRequest(Principal("alice", "human"), "task:create", "task:collection"),
                AuthorizationDecision(False, "decision-1", "revision-1", "denied"),
            )
            self.assertTrue(audit.verify_chain())
            with sqlite3.connect(path) as db:
                db.execute("UPDATE authorization_audit_events SET reason_code='tampered'")
            self.assertFalse(audit.verify_chain())


    def test_independent_checkpoint_detects_truncation_and_rewritten_history(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                if self.value is not None and checkpoint.event_id < self.value.event_id:
                    raise ValueError("checkpoint_rollback")
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            audit = SQLiteAuthorizationAuditLog(path)
            request = AuthorizationRequest(Principal("worker", "workload"), "task:read", "task:one")
            decision = AuthorizationDecision(True, "decision-1", "revision-1", "allowed")
            audit(request, decision)
            anchor = MemoryAnchor()
            checkpoint = audit.publish_checkpoint(anchor)
            self.assertEqual(checkpoint.event_id, 1)
            self.assertTrue(audit.verify_anchor(anchor))
            audit(request, AuthorizationDecision(False, "decision-2", "revision-1", "denied"))
            self.assertTrue(audit.verify_anchor(anchor))
            with sqlite3.connect(path) as db:
                db.execute("DELETE FROM authorization_audit_events WHERE event_id=1")
            self.assertFalse(audit.verify_anchor(anchor))

    def test_checkpoint_rejects_untrusted_anchor_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "authorization.sqlite3"))
            class InvalidAnchor:
                def publish(self, checkpoint):
                    pass
                def latest(self):
                    return {"event_id": 0, "event_hash": "0" * 64}
            self.assertFalse(audit.verify_anchor(InvalidAnchor()))

    def test_anchor_backend_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "authorization.sqlite3"))
            class UnavailableAnchor:
                def publish(self, checkpoint):
                    raise OSError("unavailable")
                def latest(self):
                    raise OSError("unavailable")
            self.assertFalse(audit.verify_anchor(UnavailableAnchor()))

    def test_concurrent_instances_append_a_single_valid_chain(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "authorization.sqlite3")
            writers = [SQLiteAuthorizationAuditLog(path) for _ in range(4)]
            def append(index):
                request = AuthorizationRequest(
                    Principal("worker-" + str(index), "workload", tenant_id="tenant-1", agent_id="agent-1"),
                    "task:read", "task:" + str(index),
                )
                return writers[index % len(writers)](
                    request, AuthorizationDecision(True, "decision-" + str(index), "revision-1", "allowed")
                )
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(append, range(40)))
            self.assertEqual(len(set(ids)), 40)
            self.assertTrue(writers[0].verify_chain())
            self.assertEqual(writers[0].head_hash(), writers[-1].head_hash())

    def test_anchor_freshness_rejects_stale_and_future_checkpoints(self):
        class MemoryAnchor:
            def __init__(self, checkpoint):
                self.value = checkpoint
            def publish(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "audit.sqlite3"))
            old = datetime.now(timezone.utc) - timedelta(minutes=10)
            anchor = MemoryAnchor(audit.checkpoint().__class__(0, "0" * 64, old.isoformat()))
            self.assertTrue(audit.verify_anchor(anchor))
            self.assertFalse(audit.verify_anchor(anchor, max_age_seconds=300))
            future = datetime.now(timezone.utc) + timedelta(minutes=2)
            anchor.value = audit.checkpoint().__class__(0, "0" * 64, future.isoformat())
            self.assertFalse(audit.verify_anchor(anchor, max_age_seconds=300))

    def test_checkpoint_publisher_refreshes_and_advances_without_bootstrap(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                if self.value is not None and checkpoint.event_id < self.value.event_id:
                    raise ValueError("checkpoint_rollback")
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "audit.sqlite3"))
            anchor = MemoryAnchor()
            with self.assertRaisesRegex(RuntimeError, "requires_operator_bootstrap"):
                AuthorizationAuditCheckpointPublisher(audit, anchor).run_once()
            anchor.publish(audit.checkpoint())  # Explicit operator bootstrap.
            publisher = AuthorizationAuditCheckpointPublisher(audit, anchor)
            refreshed = publisher.run_once()
            self.assertEqual(refreshed.event_id, 0)
            self.assertTrue(audit.verify_anchor(anchor, max_age_seconds=30))
            request = AuthorizationRequest(Principal("worker", "workload"), "task:read", "task:1")
            audit(request, AuthorizationDecision(True, "decision-1", "revision-1", "allowed"))
            advanced = publisher.run_once()
            self.assertEqual(advanced.event_id, 1)
            self.assertTrue(audit.verify_anchor(anchor, max_age_seconds=30))

    def test_checkpoint_publisher_rejects_local_history_behind_anchor(self):
        class MemoryAnchor:
            def __init__(self, checkpoint):
                self.value = checkpoint
            def publish(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "audit.sqlite3"))
            anchor = MemoryAnchor(audit.checkpoint().__class__(3, "a" * 64, datetime.now(timezone.utc).isoformat()))
            with self.assertRaisesRegex(RuntimeError, "integrity_mismatch"):
                AuthorizationAuditCheckpointPublisher(audit, anchor).run_once()

    def test_backup_verification_accepts_anchored_backup_read_only(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            live_path = str(Path(directory) / "live.sqlite3")
            backup_path = str(Path(directory) / "backup.sqlite3")
            live = SQLiteAuthorizationAuditLog(live_path)
            live(
                AuthorizationRequest(Principal("worker", "workload"), "task:complete", "task:7"),
                AuthorizationDecision(True, "decision-7", "revision-1", "allowed"),
            )
            with sqlite3.connect(live_path) as source, sqlite3.connect(backup_path) as backup:
                source.backup(backup)
            anchor = MemoryAnchor()
            live.publish_checkpoint(anchor)
            before = Path(backup_path).read_bytes()
            self.assertTrue(SQLiteAuthorizationAuditLog.verify_backup(
                backup_path, anchor, max_age_seconds=300,
            ))
            self.assertEqual(Path(backup_path).read_bytes(), before)

    def test_backup_verification_rejects_backup_behind_external_anchor(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            live_path = str(Path(directory) / "live.sqlite3")
            backup_path = str(Path(directory) / "older.sqlite3")
            live = SQLiteAuthorizationAuditLog(live_path)
            with sqlite3.connect(live_path) as source, sqlite3.connect(backup_path) as backup:
                source.backup(backup)
            live(
                AuthorizationRequest(Principal("worker", "workload"), "task:complete", "task:8"),
                AuthorizationDecision(True, "decision-8", "revision-1", "allowed"),
            )
            anchor = MemoryAnchor()
            live.publish_checkpoint(anchor)
            self.assertFalse(SQLiteAuthorizationAuditLog.verify_backup(backup_path, anchor))

    def test_backup_verification_rejects_tampering_and_missing_files_without_creating_them(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            live_path = str(Path(directory) / "live.sqlite3")
            backup_path = str(Path(directory) / "backup.sqlite3")
            missing_path = str(Path(directory) / "missing.sqlite3")
            live = SQLiteAuthorizationAuditLog(live_path)
            live(
                AuthorizationRequest(Principal("worker", "workload"), "task:complete", "task:9"),
                AuthorizationDecision(True, "decision-9", "revision-1", "allowed"),
            )
            with sqlite3.connect(live_path) as source, sqlite3.connect(backup_path) as backup:
                source.backup(backup)
            anchor = MemoryAnchor()
            live.publish_checkpoint(anchor)
            with sqlite3.connect(backup_path) as db:
                db.execute("UPDATE authorization_audit_events SET reason_code='forged'")
            self.assertFalse(SQLiteAuthorizationAuditLog.verify_backup(backup_path, anchor))
            self.assertFalse(SQLiteAuthorizationAuditLog.verify_backup(missing_path, anchor))
            self.assertFalse(Path(missing_path).exists())

class HttpsAuthorizationAuditAnchorTests(unittest.TestCase):
    def _anchor(self):
        context = ssl.create_default_context()
        return HttpsAuthorizationAuditAnchor(
            "https://audit.example.test/v1/checkpoint",
            ssl_context=context,
            bearer_token_provider=lambda: "short-lived-test-token",
        )

    @staticmethod
    def _response(status, payload, etag):
        class Response:
            def __init__(self):
                self.status = status
                self.headers = {"ETag": etag}
            def read(self, size=-1):
                return payload[:size]
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
        return Response()

    def test_https_anchor_uses_etag_compare_and_set_and_server_timestamp(self):
        anchor = self._anchor()
        published_at = datetime.now(timezone.utc).isoformat()
        body = json.dumps({
            "event_id": 4, "event_hash": "a" * 64, "published_at": published_at,
        }).encode()
        responses = [
            self._response(200, body, '"etag-1"'),
            self._response(200, body, '"etag-2"'),
            self._response(200, body, '"etag-2"'),
        ]
        requests = []
        def fake_urlopen(request, **kwargs):
            requests.append(request)
            return responses.pop(0)
        with patch.object(anchor._opener, "open", side_effect=fake_urlopen):
            current = anchor.latest()
            anchor.publish(AuthorizationAuditCheckpoint(4, "a" * 64, "local-time-is-not-authoritative"))
            confirmed = anchor.latest()
        self.assertEqual(current.event_id, 4)
        self.assertEqual(confirmed.published_at, published_at)
        self.assertEqual(requests[1].get_header("If-match"), '"etag-1"')
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer short-lived-test-token")
        self.assertNotIn("token", requests[1].full_url)

    def test_https_anchor_rejects_tls_context_without_hostname_verification(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with self.assertRaisesRegex(ValueError, "must_verify_certificate"):
            HttpsAuthorizationAuditAnchor(
                "https://audit.example.test/v1/checkpoint",
                ssl_context=context,
                bearer_token_provider=lambda: "token",
            )

    def test_https_anchor_compare_and_set_conflict_fails_closed_without_retry(self):
        anchor = self._anchor()
        published_at = datetime.now(timezone.utc).isoformat()
        body = json.dumps({
            "event_id": 1, "event_hash": "b" * 64, "published_at": published_at,
        }).encode()
        requests = []
        def fake_urlopen(request, **kwargs):
            requests.append(request)
            if request.method == "GET":
                return self._response(200, body, '"etag-1"')
            raise HTTPError(request.full_url, 412, "conflict", {}, BytesIO(b""))
        with patch.object(anchor._opener, "open", side_effect=fake_urlopen):
            anchor.latest()
            with self.assertRaisesRegex(RuntimeError, "compare_and_set_conflict"):
                anchor.publish(AuthorizationAuditCheckpoint(2, "c" * 64))
        self.assertEqual(len(requests), 2)

    def test_https_anchor_bootstrap_uses_create_only_precondition(self):
        anchor = self._anchor()
        published_at = datetime.now(timezone.utc).isoformat()
        body = json.dumps({
            "event_id": 0, "event_hash": "0" * 64, "published_at": published_at,
        }).encode()
        requests = []
        def fake_open(request, **kwargs):
            requests.append(request)
            if request.method == "GET":
                raise HTTPError(request.full_url, 404, "not provisioned", {}, BytesIO(b""))
            return self._response(201, body, '"etag-created"')
        with patch.object(anchor._opener, "open", side_effect=fake_open):
            self.assertIsNone(anchor.latest())
            anchor.publish(AuthorizationAuditCheckpoint(0, "0" * 64))
        self.assertEqual(requests[1].get_header("If-none-match"), "*")
        self.assertIsNone(requests[1].get_header("If-match"))

    def test_https_anchor_rejects_redirects(self):
        from core.authorization_audit import _RejectAuditAnchorRedirects
        handler = _RejectAuditAnchorRedirects()
        with self.assertRaisesRegex(RuntimeError, "redirect_rejected"):
            handler.redirect_request(None, None, 302, "Found", {}, "https://other.example.test/")


if __name__ == "__main__":
    unittest.main()
