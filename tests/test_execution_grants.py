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

    def test_live_policy_revocation_blocks_host_tool_dispatch(self):
        policy = {"allowed": True}
        authority = HMACExecutionGrantAuthority(
            str(Path(self.tmp.name) / "policy.sqlite3"),
            key_id="policy-key-v1",
            signing_key=self.key,
            authorize_request=lambda request: policy["allowed"],
        )
        grant = authority.issue(self.request)
        policy["allowed"] = False
        dispatched = []
        with self.assertRaisesRegex(ValueError, "policy no longer authorizes"):
            authority.authorize_tool_call(
                grant,
                self.request,
                tool_name="workspace.read",
                operation=lambda: dispatched.append(True) or {"ok": True},
            )
        self.assertEqual(dispatched, [])

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

    def test_execution_grant_can_only_be_consumed_once(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        self.assertTrue(self.authority.verify(grant, self.request))
        self.assertFalse(self.authority.consume_execution(grant, self.request))

    def test_concurrent_execution_consumption_has_single_winner(self):
        grant = self.authority.issue(self.request)
        barrier = threading.Barrier(2)
        outcomes = []
        errors = []

        def consume():
            try:
                barrier.wait(timeout=2)
                outcomes.append(self.authority.consume_execution(grant, self.request))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=consume) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(outcomes), [False, True])

    def test_remote_tool_call_is_durable_and_idempotent_by_call_index(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        dispatched = []

        first = self.authority.authorize_remote_tool_call(
            grant, self.request, call_index=0, tool_name="workspace.read",
            arguments={"path": "README.md"},
            operation=lambda: dispatched.append("first") or {"content": "safe"},
        )
        replay = self.authority.authorize_remote_tool_call(
            grant, self.request, call_index=0, tool_name="workspace.read",
            arguments={"path": "README.md"},
            operation=lambda: dispatched.append("replayed") or {"content": "unsafe"},
        )
        self.assertEqual(first, {"content": "safe"})
        self.assertEqual(replay, first)
        self.assertEqual(dispatched, ["first"])
        with self.assertRaisesRegex(ValueError, "different input"):
            self.authority.authorize_remote_tool_call(
                grant, self.request, call_index=0, tool_name="workspace.read",
                arguments={"path": "secrets.txt"},
                operation=lambda: dispatched.append("tampered") or {"content": "bad"},
            )
        self.assertEqual(dispatched, ["first"])

    def test_failed_remote_tool_call_cannot_be_replayed(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        dispatched = []

        def fail_once():
            dispatched.append("attempt")
            raise RuntimeError("transient host failure")

        with self.assertRaisesRegex(ValueError, "remote host tool dispatch failed"):
            self.authority.authorize_remote_tool_call(
                grant, self.request, call_index=0, tool_name="workspace.read",
                arguments={"path": "README.md"}, operation=fail_once,
            )
        with self.assertRaisesRegex(ValueError, "pending or previously failed"):
            self.authority.authorize_remote_tool_call(
                grant, self.request, call_index=0, tool_name="workspace.read",
                arguments={"path": "README.md"},
                operation=lambda: dispatched.append("replay") or {"content": "bad"},
            )
        self.assertEqual(dispatched, ["attempt"])

    def test_signed_grant_can_be_resolved_from_source_free_manifest(self):
        grant = self.authority.issue(self.request)
        manifest = self.request.manifest
        resolved = self.authority.resolve_grant(grant.token, manifest)
        self.assertEqual(resolved, grant)
        altered = type(manifest)(
            execution_id=manifest.execution_id,
            actor_id=manifest.actor_id,
            session_id=manifest.session_id,
            task_id=manifest.task_id,
            source_sha256="0" * 64,
            tool_names=manifest.tool_names,
            timeout_seconds=manifest.timeout_seconds,
            max_tool_calls=manifest.max_tool_calls,
            max_result_bytes=manifest.max_result_bytes,
            network_access=False,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.authority.resolve_grant(grant.token, altered)

    def test_remote_ledger_accepts_source_free_manifest_binding(self):
        grant = self.authority.issue(self.request)
        manifest = self.request.manifest
        self.assertTrue(self.authority.consume_execution(grant, manifest))
        result = self.authority.authorize_remote_tool_call(
            grant, manifest, call_index=0, tool_name="workspace.read",
            arguments={"path": "README.md"}, operation=lambda: {"content": "safe"},
        )
        self.assertEqual(result, {"content": "safe"})
        self.assertTrue(self.authority.record_remote_completion(
            grant, manifest, status="succeeded", tool_calls=1, duration_seconds=1.0,
        ))

    def test_remote_tool_call_requires_consumed_grant(self):
        grant = self.authority.issue(self.request)
        with self.assertRaisesRegex(ValueError, "not consumed"):
            self.authority.authorize_remote_tool_call(
                grant, self.request, call_index=0, tool_name="workspace.read",
                arguments={"path": "README.md"}, operation=lambda: {"content": "safe"},
            )

    def test_remote_completion_is_durable_and_idempotent(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        self.authority.authorize_remote_tool_call(
            grant, self.request, call_index=0, tool_name="workspace.read",
            arguments={"path": "README.md"}, operation=lambda: {"content": "safe"},
        )
        self.assertTrue(self.authority.record_remote_completion(
            grant, self.request, status="succeeded", tool_calls=1, duration_seconds=1.25,
        ))
        self.assertTrue(self.authority.record_remote_completion(
            grant, self.request, status="succeeded", tool_calls=1, duration_seconds=1.25,
        ))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.authority.record_remote_completion(
                grant, self.request, status="failed", tool_calls=1, duration_seconds=1.25,
            )

        with self.assertRaisesRegex(ValueError, "already terminal"):
            self.authority.authorize_remote_tool_call(
                grant, self.request, call_index=0, tool_name="workspace.read",
                arguments={"path": "README.md"}, operation=lambda: {"content": "late"},
            )

    def test_failed_remote_completion_records_native_and_reported_counts(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        self.assertTrue(self.authority.record_remote_completion(
            grant, self.request, status="failed", tool_calls=1, duration_seconds=0.5,
        ))
        import sqlite3
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT tool_calls, reported_tool_calls FROM execution_completions WHERE execution_id=?",
                (self.request.execution_id,),
            ).fetchone()
        self.assertEqual(row, (0, 1))

    def test_successful_remote_completion_rejects_unrecorded_tool_call(self):
        grant = self.authority.issue(self.request)
        self.assertTrue(self.authority.consume_execution(grant, self.request))
        with self.assertRaisesRegex(ValueError, "does not match native records"):
            self.authority.record_remote_completion(
                grant, self.request, status="succeeded", tool_calls=1,
            )

    def test_token_tampering_and_wrong_signing_key_fail_closed(self):
        grant = self.authority.issue(self.request)
        parts = grant.token.split(".")
        tampered = replace(grant, token=".".join(parts[:-1] + ["AAAA"]))
        self.assertFalse(self.authority.verify(tampered, self.request))
        wrong_key = HMACExecutionGrantAuthority(
            self.path,
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
