"""Durable grant issuance, verification, and revocation tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.code_execution import CodeExecutionRequest
from core.execution_grants import HMACExecutionGrantAuthority


class ExecutionGrantAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "grants.sqlite3")
        self.key = b"test-only-signing-key-material-32-bytes!"
        self.authority = HMACExecutionGrantAuthority(
            self.path, key_id="test-key-v1", signing_key=self.key,
            authorize_request=lambda request: True,
        )
        self.request = CodeExecutionRequest(
            execution_id="exec-grant-01",
            actor_id="actor-a",
            session_id="session-a",
            task_id="task-a",
            source="result = 42",
            tool_names=("workspace.read",),
            timeout_seconds=10,
            max_tool_calls=1,
            max_result_bytes=128,
        )

    def test_issue_and_verify_exact_request(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.verify(grant, self.request))
        self.assertEqual(grant.allowed_tools, ("workspace.read",))
        self.assertNotIn(self.key.decode(), grant.token)
        import sqlite3
        with sqlite3.connect(self.path) as db:
            events = db.execute(
                "SELECT event_kind FROM execution_grant_events WHERE execution_id=? ORDER BY event_id",
                (self.request.execution_id,),
            ).fetchall()
        self.assertEqual(events, [("issued",)])

    def test_grant_is_bound_to_actor_session_task_and_manifest(self):
        grant = self.authority.issue(self.request)
        altered_requests = (
            replace(self.request, actor_id="actor-b"),
            replace(self.request, session_id="session-b"),
            replace(self.request, task_id="task-b"),
            replace(self.request, source="result = 43"),
        )
        for request in altered_requests:
            with self.subTest(request=request):
                self.assertFalse(self.authority.verify(grant, request))

    def test_grant_issuance_requires_positive_yuki_policy_authorization(self):
        denied = HMACExecutionGrantAuthority(
            str(Path(self.tmp.name) / "denied.sqlite3"),
            key_id="test-key-v1",
            signing_key=self.key,
            authorize_request=lambda request: False,
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            denied.issue(self.request)

    def test_grant_cannot_be_issued_twice_for_execution_id(self):
        self.authority.issue(self.request)
        with self.assertRaises(ValueError):
            self.authority.issue(self.request)

    def test_revocation_is_durable_and_actor_scoped(self):
        grant = self.authority.issue(self.request)
        self.assertFalse(self.authority.revoke(self.request.execution_id, actor_id="actor-b"))
        self.assertTrue(self.authority.verify(grant, self.request))
        self.assertTrue(self.authority.revoke(self.request.execution_id, actor_id="actor-a"))
        self.assertFalse(self.authority.verify(grant, self.request))
        import sqlite3
        with sqlite3.connect(self.path) as db:
            events = db.execute(
                "SELECT event_kind FROM execution_grant_events WHERE execution_id=? ORDER BY event_id",
                (self.request.execution_id,),
            ).fetchall()
        self.assertEqual(events, [("issued",), ("revoked",)])
        reopened = HMACExecutionGrantAuthority(
            self.path, key_id="test-key-v1", signing_key=self.key,
            authorize_request=lambda request: True,
        )
        self.assertFalse(reopened.verify(grant, self.request))

    def test_revocation_cannot_race_between_tool_authorization_and_dispatch(self):
        grant = self.authority.issue(self.request)
        operation_entered = threading.Event()
        release_operation = threading.Event()
        revoke_started = threading.Event()
        revoke_finished = threading.Event()
        errors = []

        def operation():
            operation_entered.set()
            if not release_operation.wait(timeout=3):
                raise TimeoutError("test operation release timed out")
            return {"ok": True}

        def dispatch():
            try:
                result = self.authority.authorize_tool_call(
                    grant, self.request, tool_name="workspace.read", operation=operation
                )
                self.assertEqual(result, {"ok": True})
            except Exception as exc:
                errors.append(exc)

        def revoke():
            revoke_started.set()
            try:
                self.authority.revoke(self.request.execution_id, actor_id="actor-a")
            except Exception as exc:
                errors.append(exc)
            finally:
                revoke_finished.set()

        dispatch_thread = threading.Thread(target=dispatch)
        dispatch_thread.start()
        self.assertTrue(operation_entered.wait(timeout=2))
        revoke_thread = threading.Thread(target=revoke)
        revoke_thread.start()
        self.assertTrue(revoke_started.wait(timeout=2))
        time.sleep(0.1)
        self.assertFalse(revoke_finished.is_set())
        release_operation.set()
        dispatch_thread.join(timeout=3)
        revoke_thread.join(timeout=3)
        self.assertFalse(dispatch_thread.is_alive())
        self.assertFalse(revoke_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(self.authority.verify(grant, self.request))

    def test_token_tampering_and_wrong_signing_key_fail_closed(self):
        grant = self.authority.issue(self.request)
        parts = grant.token.split(".")
        tampered = replace(grant, token=".".join(parts[:-1] + ["AAAA"]))
        self.assertFalse(self.authority.verify(tampered, self.request))
        wrong_key = HMACExecutionGrantAuthority(
            str(Path(self.tmp.name) / "other.sqlite3"),
            key_id="test-key-v1",
            signing_key=b"different-signing-key-material-32b",
            authorize_request=lambda request: True,
        )
        self.assertFalse(wrong_key.verify(grant, self.request))

    def test_expired_grant_fails_even_if_database_row_exists(self):
        grant = self.authority.issue(self.request, ttl_seconds=1)
        # Use an altered grant expiry to prove token claims are exact-bound.
        expired = replace(
            grant,
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        self.assertFalse(self.authority.verify(expired, self.request))

    def test_short_signing_keys_and_invalid_ttl_are_rejected(self):
        with self.assertRaises(ValueError):
            HMACExecutionGrantAuthority(
                str(Path(self.tmp.name) / "weak.sqlite3"),
                key_id="weak",
                signing_key=b"short",
                authorize_request=lambda request: True,
            )
        for ttl in (0, 901, True):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    self.authority.issue(
                        replace(self.request, execution_id=f"exec-ttl-{ttl}"),
                        ttl_seconds=ttl,
                    )


if __name__ == "__main__":
    unittest.main()
