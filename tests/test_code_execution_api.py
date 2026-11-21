from __future__ import annotations

from dataclasses import asdict
import json
import tempfile
import unittest
from pathlib import Path

from automation.authorized_read_tools import AuthorizedReadOnlyTools, ToolPrincipal
from core.auth import BuiltInAuthService
from core.code_execution import CodeExecutionRequest
from core.code_execution_tools import AuthenticatedReadToolDispatcher
from core.execution_grants import HMACExecutionGrantAuthority
from api.code_execution import AuthenticatedRemoteCodeExecutionAPI


async def invoke(app, path, payload, *, token="test-adapter-secret-material-32-bytes"):
    body = json.dumps(payload).encode("utf-8")
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-yuki-adapter-token", token.encode("utf-8")),
        ],
    }
    await app(scope, receive, send)
    return sent[0]["status"], json.loads(sent[1]["body"])


class RemoteCodeExecutionAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "README.md").write_text("native bridge", encoding="utf-8")
        self.auth = BuiltInAuthService(
            str(root / "auth.sqlite3"),
            password_iterations=100_000,
            session_ttl_seconds=3600,
            max_failed_logins=3,
            lockout_seconds=30,
        )
        self.user = self.auth.create_account("bridge.user", "A sufficiently long password 5!")
        session = self.auth.authenticate("bridge.user", "A sufficiently long password 5!")
        self.session_token = session.token
        import hashlib
        fingerprint = hashlib.sha256(session.token.encode("utf-8")).hexdigest()
        self.request = CodeExecutionRequest(
            execution_id="remote-exec-001",
            actor_id=self.user.user_id,
            session_id=fingerprint,
            task_id="remote-task-001",
            source="const file = await workspace.read({path: 'README.md'}); return file;",
            tool_names=("workspace.read",),
            timeout_seconds=20,
            max_tool_calls=2,
            max_result_bytes=4096,
        )
        self.grant_authority = HMACExecutionGrantAuthority(
            str(root / "grants.sqlite3"),
            key_id="bridge-test-key",
            signing_key=b"bridge-test-signing-key-material-32-bytes",
            authorize_request=lambda _request: True,
        )
        registry = AuthorizedReadOnlyTools(
            self.workspace,
            audit=lambda _event: None,
            principal_is_trusted=lambda _principal: True,
        )
        self.dispatcher = AuthenticatedReadToolDispatcher(
            registry,
            principal_resolver=lambda request: ToolPrincipal(
                request.actor_id, frozenset({"workspace:read"})
            ),
        )
        self.audit_events = []
        self.app = AuthenticatedRemoteCodeExecutionAPI(
            lambda scope, receive, send: None,
            auth_service=self.auth,
            grant_authority=self.grant_authority,
            dispatcher=self.dispatcher,
            adapter_token="test-adapter-secret-material-32-bytes",
            audit=lambda event, record: self.audit_events.append((event, dict(record))),
        )
        self.grant = self.grant_authority.issue(self.request)
        self.manifest_payload = {
            **asdict(self.request.manifest),
            "manifest_sha256": self.request.manifest_sha256,
            "grant": self.grant.token,
        }

    async def test_authorize_tool_call_and_completion_use_native_authority(self):
        status, body = await invoke(
            self.app, "/v1/internal/executions/authorize", self.manifest_payload
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["authorized"])

        status, body = await invoke(
            self.app,
            "/v1/internal/executions/tool-call",
            {
                **self.manifest_payload,
                "tool_name": "workspace.read",
                "call_index": 0,
                "arguments": {"path": "README.md"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["content"], "native bridge")

        status, body = await invoke(
            self.app,
            "/v1/internal/executions/complete",
            {
                **self.manifest_payload,
                "status": "succeeded",
                "tool_calls": 1,
                "duration_seconds": 1.5,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["authorized"])
        self.assertEqual(
            [event for event, _record in self.audit_events],
            [
                "code_execution.started",
                "code_execution.tool_requested",
                "code_execution.tool_completed",
                "code_execution.completed",
            ],
        )

        replay_status, replay = await invoke(
            self.app, "/v1/internal/executions/authorize", self.manifest_payload
        )
        self.assertEqual(replay_status, 409)
        self.assertFalse(replay["authorized"])

        late_status, late = await invoke(
            self.app,
            "/v1/internal/executions/tool-call",
            {
                **self.manifest_payload,
                "tool_name": "workspace.read",
                "call_index": 0,
                "arguments": {"path": "README.md"},
            },
        )
        self.assertEqual(late_status, 403)
        self.assertFalse(late["authorized"])

    async def test_session_revocation_blocks_tool_calls_but_not_terminal_reconciliation(self):
        status, _ = await invoke(
            self.app, "/v1/internal/executions/authorize", self.manifest_payload
        )
        self.assertEqual(status, 200)
        self.auth.revoke_session(self.session_token)

        tool_status, _ = await invoke(
            self.app,
            "/v1/internal/executions/tool-call",
            {
                **self.manifest_payload,
                "tool_name": "workspace.read",
                "call_index": 0,
                "arguments": {"path": "README.md"},
            },
        )
        self.assertEqual(tool_status, 403)

        completion_status, completion = await invoke(
            self.app,
            "/v1/internal/executions/complete",
            {
                **self.manifest_payload,
                "status": "failed",
                "tool_calls": 1,
                "duration_seconds": 1.0,
            },
        )
        self.assertEqual(completion_status, 200)
        self.assertTrue(completion["authorized"])

    async def test_adapter_secret_is_required(self):
        status, body = await invoke(
            self.app,
            "/v1/internal/executions/authorize",
            self.manifest_payload,
            token="wrong-adapter-secret",
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "adapter_authentication_required")


if __name__ == "__main__":
    unittest.main()
