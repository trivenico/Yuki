"""Authenticated HTTP tests for owner/session-scoped approval endpoints."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.owner_approval_factory import OwnerApprovalRuntimeFactory


class _DenyAll:
    def authorize_tool(self, name):
        return False


class _ApprovedPolicy:
    def authorize_tool(self, name):
        return False

    def skill_id_for_tool(self, name):
        return "notes.write" if name == "notes.write" else None

    def authorize_approved_tool_context(self, name, skill_id, context):
        return name == "notes.write" and skill_id == "notes.write" and bool(context.get("actor"))


class _Executor:
    def __init__(self):
        self.executed = []
        self.runtime = GuardedToolRuntime(
            {"notes.write": lambda raw: self.executed.append(json.loads(raw)) or "executed"},
            _ApprovedPolicy(), audit=lambda *_: None, require_audit=True,
        )


def _loop():
    return AgentExecutionLoop(
        lambda **kwargs: {"text": "ready", "tool_calls": {"tool_calls": []}},
        StructuredToolProtocol(GuardedToolRuntime({}, _DenyAll())),
    )


class AuthenticatedApprovalHTTPTests(unittest.TestCase):
    def test_list_approve_and_deny_are_bound_to_authenticated_owner_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            alice = auth.create_account("alice", "alice-secure-password")
            bob = auth.create_account("bob", "bob-secure-password")
            alice_session = auth.authenticate("alice", "alice-secure-password")
            alice_second_session = auth.authenticate("alice", "alice-secure-password")
            bob_session = auth.authenticate("bob", "bob-secure-password")
            executor = _Executor()
            approvals = OwnerApprovalRuntimeFactory(
                root / "approvals",
                planner_resolver_for_owner=lambda owner, session: executor.runtime if owner == alice.user_id else None,
            )
            alice_session_hash = hashlib.sha256(alice_session.token.encode()).hexdigest()
            coordinator = approvals.coordinator_for_owner(alice.user_id)
            pending = coordinator.record(
                None,
                SimpleNamespace(action={
                    "skill_id": "notes.write", "tool_name": "notes.write",
                    "arguments": {"text": "approved note"},
                    "execution_context": {
                        "actor": alice.user_id, "session_id": alice_session_hash,
                        "conversation_id": "conversation-a", "request_id": "request-a",
                    },
                }),
                actor=alice.user_id, session_id=alice_session_hash,
                idempotency_key="conversation-a:request-a",
            )

            recovery = coordinator.pending_store.put(
                "notes.write", {"text": "uncertain"},
                {"skill_id": "notes.write", "tool_name": "notes.write",
                 "arguments": {"text": "uncertain"}},
                actor=alice.user_id, session_id=alice_session_hash,
            )
            for expected, new in (("pending", "claimed"), ("claimed", "approved"),
                                  ("approved", "executing"), ("executing", "outcome_unknown")):
                coordinator.pending_store.transition(
                    recovery.pending_id, expected_status=expected, new_status=new,
                    actor=alice.user_id, session_id=alice_session_hash,
                )

            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                approval_runtime_factory=approvals,
            )

            async def request(method, path, token, payload=None):
                body = json.dumps(payload).encode() if payload is not None else b""
                sent = []
                consumed = False

                async def receive():
                    nonlocal consumed
                    if consumed:
                        return {"type": "http.disconnect"}
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}

                async def send(message):
                    sent.append(message)

                await runtime.app({
                    "type": "http", "method": method, "path": path,
                    "headers": [(b"authorization", ("Bearer " + token).encode())],
                    "query_string": b"",
                }, receive, send)
                return sent[0]["status"], json.loads(sent[1]["body"])

            async def scenario():
                alice_list = await request("GET", "/v1/approvals", alice_session.token)
                bob_list = await request("GET", "/v1/approvals", bob_session.token)
                alice_recovery = await request("GET", "/v1/approvals/recovery", alice_session.token)
                alice_other_session_list = await request("GET", "/v1/approvals", alice_second_session.token)
                alice_other_session_recovery = await request("GET", "/v1/approvals/recovery", alice_second_session.token)
                bob_recovery = await request("GET", "/v1/approvals/recovery", bob_session.token)
                bob_cross = await request(
                    "POST", f"/v1/approvals/{pending.pending_id}/approve",
                    bob_session.token, {"confirm_action": True},
                )
                alice_approved = await request(
                    "POST", f"/v1/approvals/{pending.pending_id}/approve",
                    alice_session.token, {"confirm_action": True},
                )
                replay = await request(
                    "POST", f"/v1/approvals/{pending.pending_id}/approve",
                    alice_session.token, {"confirm_action": True},
                )
                return (alice_list, bob_list, alice_recovery, alice_other_session_list,
                        alice_other_session_recovery, bob_recovery, bob_cross, alice_approved, replay)

            try:
                (alice_list, bob_list, alice_recovery, alice_other_session_list,
                 alice_other_session_recovery, bob_recovery, bob_cross, approved, replay) = asyncio.run(scenario())
                self.assertEqual(alice_list[0], 200)
                self.assertEqual(len(alice_list[1]["approvals"]), 1)
                self.assertEqual(alice_list[1]["approvals"][0]["pending_id"], pending.pending_id)
                self.assertEqual(bob_list, (200, {"approvals": []}))
                self.assertEqual(alice_recovery[0], 200)
                self.assertEqual(alice_recovery[1]["recovery"][0]["pending_id"], recovery.pending_id)
                self.assertTrue(alice_recovery[1]["recovery"][0]["requires_reconciliation"])
                self.assertEqual(bob_recovery, (200, {"recovery": []}))
                # A second bearer session for the same owner is isolated too.
                self.assertEqual(alice_other_session_list, (200, {"approvals": []}))
                self.assertEqual(alice_other_session_recovery, (200, {"recovery": []}))
                # Listing recovery is read-only: it neither resolves nor retries it.
                retained = coordinator.pending_store.get_for_identity(
                    recovery.pending_id, actor=alice.user_id, session_id=alice_session_hash,
                )
                self.assertEqual(retained.status, "outcome_unknown")
                self.assertEqual(executor.executed, [])
                self.assertEqual(bob_cross[0], 404)
                self.assertEqual(approved, (200, {
                    "ok": True, "status": "completed", "pending_id": pending.pending_id,
                }))
                self.assertEqual(replay[0], 409)
                self.assertEqual(replay[1]["status"], "recovery_required")
                self.assertEqual(len(executor.executed), 1)
            finally:
                runtime.close()

    def test_approval_requires_literal_confirmation_and_deny_is_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
            owner = auth.create_account("owner", "owner-secure-password")
            session = auth.authenticate("owner", "owner-secure-password")
            executor = _Executor()
            approvals = OwnerApprovalRuntimeFactory(
                root / "approvals", planner_resolver_for_owner=lambda *_: executor.runtime,
            )
            session_hash = hashlib.sha256(session.token.encode()).hexdigest()
            pending = approvals.coordinator_for_owner(owner.user_id).record(
                None, SimpleNamespace(action={
                    "skill_id": "notes.write", "tool_name": "notes.write",
                    "arguments": {"text": "no"},
                    "execution_context": {
                        "actor": owner.user_id, "session_id": session_hash,
                        "conversation_id": "conversation-b", "request_id": "request-b",
                    },
                }), actor=owner.user_id, session_id=session_hash,
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                approval_runtime_factory=approvals,
            )

            async def request(method, path, payload):
                body = json.dumps(payload).encode()
                sent = []
                used = False
                async def receive():
                    nonlocal used
                    if used:
                        return {"type": "http.disconnect"}
                    used = True
                    return {"type": "http.request", "body": body, "more_body": False}
                async def send(message):
                    sent.append(message)
                await runtime.app({
                    "type": "http", "method": method, "path": path,
                    "headers": [(b"authorization", ("Bearer " + session.token).encode())],
                    "query_string": b"",
                }, receive, send)
                return sent[0]["status"], json.loads(sent[1]["body"])

            async def scenario():
                invalid = await request(
                    "POST", f"/v1/approvals/{pending.pending_id}/approve",
                    {"confirm_action": "true"},
                )
                denied = await request(
                    "POST", f"/v1/approvals/{pending.pending_id}/deny", {},
                )
                return invalid, denied

            try:
                invalid, denied = asyncio.run(scenario())
                self.assertEqual(invalid[0], 400)
                self.assertEqual(denied, (200, {
                    "ok": True, "status": "approval_denied", "pending_id": pending.pending_id,
                }))
                self.assertEqual(executor.executed, [])
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
