"""Composition tests for the trusted code-execution control plane."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.approval_audit import SQLiteApprovalAuditLog
from core.code_execution import CodeExecutionRequest
from core.code_execution_runtime import (
    CodeExecutionAuditRecorder,
    build_code_execution_runtime,
    derive_execution_grant_key,
)


class _KeyProvider:
    def hmac_key(self):
        return b"stable-test-os-key-store-master-key-32"


class _Executor:
    def execute(self, request, grant, *, dispatch_tool, cancellation_requested):
        raise AssertionError("runtime composition test must not execute generated code")


class CodeExecutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.grants = str(root / "grants.sqlite3")
        self.request = CodeExecutionRequest(
            execution_id="runtime-execution-01",
            actor_id="account-user-01",
            session_id="session-01",
            task_id="task-01",
            source="result = 42",
            tool_names=(),
            max_tool_calls=0,
        )

    def test_key_derivation_is_stable_and_domain_separated(self):
        master = _KeyProvider().hmac_key()
        first = derive_execution_grant_key(master)
        self.assertEqual(first, derive_execution_grant_key(master))
        self.assertNotEqual(first, master)
        self.assertNotEqual(first, derive_execution_grant_key(master + b"x"))

    def test_runtime_uses_injected_os_key_provider_and_live_policy(self):
        authorized = []
        runtime = build_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            authorize_request=lambda request: authorized.append(request.actor_id) or True,
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        grant = runtime.grant_authority.issue(self.request)
        self.assertTrue(runtime.grant_authority.verify(grant, self.request))
        self.assertEqual(authorized, ["account-user-01"])

    def test_denied_policy_prevents_grant_issuance(self):
        runtime = build_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            authorize_request=lambda request: False,
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            runtime.grant_authority.issue(self.request)

    def test_execution_events_flow_into_existing_hash_chained_audit(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        record = {
            "actor_id": "account-user-01",
            "execution_id": "runtime-execution-01",
            "task_id": "task-01",
            "session_id": "session-01",
            "manifest_sha256": "a" * 64,
        }
        recorder("code_execution.started", record)
        recorder("code_execution.tool_requested", record)
        recorder("code_execution.tool_completed", record)
        recorder("code_execution.completed", record)
        events = list(reversed(self.audit.list_events()))
        self.assertEqual(
            [event["event_type"] for event in events],
            ["execution_started", "requested", "executed", "executed"],
        )
        self.assertTrue(self.audit.verify_chain())
        self.assertTrue(all(event["pending_id_hash"] for event in events))
        self.assertTrue(all(event["actor_hash"] for event in events))

    def test_audit_correlation_hash_accepts_maximum_length_execution_id(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        recorder("code_execution.started", {
            "actor_id": "account-user-01",
            "execution_id": "exec-" + "x" * 155,
        })
        self.assertTrue(self.audit.verify_chain())
        self.assertEqual(len(self.audit.list_events()[0]["pending_id_hash"]), 64)

    def test_runtime_refuses_a_tampered_existing_audit_chain(self):
        self.audit.append(
            event_type="requested",
            pending_id="prior-event",
            actor="account-user-01",
            skill_id="test",
        )
        with sqlite3.connect(self.audit.database_path) as db:
            db.execute(
                "UPDATE approval_audit_events SET event_hash=? WHERE event_id=1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(RuntimeError, "audit chain verification failed"):
            build_code_execution_runtime(
                _Executor(),
                database_path=self.grants,
                authorize_request=lambda request: True,
                audit_log=self.audit,
                key_provider=_KeyProvider(),
            )

    def test_unknown_audit_event_fails_closed(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            recorder("code_execution.debug_dump", {
                "actor_id": "account-user-01",
                "execution_id": "runtime-execution-01",
            })


if __name__ == "__main__":
    unittest.main()
