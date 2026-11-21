"""Tests for the audited code-execution control-plane boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.code_execution import (
    CodeExecutionContractError,
    CodeExecutionRequest,
    CodeExecutionResult,
    ExecutionGrant,
)
from core.code_execution_service import CodeExecutionService


class _Verifier:
    def __init__(self, *, revoke_on_call=None):
        self.calls = 0
        self.revoke_on_call = revoke_on_call

    def verify(self, grant, request):
        self.calls += 1
        return self.calls != self.revoke_on_call


class _Executor:
    def __init__(self, *, tool_name="workspace.read"):
        self.calls = 0
        self.tool_name = tool_name

    def execute(self, request, grant, *, dispatch_tool, cancellation_requested):
        self.calls += 1
        value = dispatch_tool(self.tool_name, {"path": "README.md"})
        return CodeExecutionResult("succeeded", str(value["content"]), 1, 0.1)


class CodeExecutionServiceTests(unittest.TestCase):
    def setUp(self):
        self.request = CodeExecutionRequest(
            execution_id="exec-01",
            actor_id="actor-01",
            session_id="session-01",
            task_id="task-01",
            source="result = tools.workspace.read({path: 'README.md'})",
            tool_names=("workspace.read",),
            timeout_seconds=10,
            max_tool_calls=2,
            max_result_bytes=128,
        )
        self.grant = ExecutionGrant(
            actor_id=self.request.actor_id,
            session_id=self.request.session_id,
            task_id=self.request.task_id,
            execution_id=self.request.execution_id,
            manifest_sha256=self.request.manifest_sha256,
            allowed_tools=("workspace.read",),
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            token="signed-test-token",
        )
        self.audit_events = []

    def _audit(self, event, metadata):
        self.audit_events.append((event, dict(metadata)))

    def test_execution_verifies_grant_and_audits_start_tool_and_completion(self):
        verifier = _Verifier()
        executor = _Executor()
        service = CodeExecutionService(executor, verifier, audit=self._audit)
        result = service.execute(
            self.request,
            self.grant,
            dispatch_tool=lambda name, args: {"content": "safe"},
            cancellation_requested=lambda: False,
        )
        self.assertEqual(result.output, "safe")
        self.assertEqual(verifier.calls, 3)  # initial, per-RPC, and completion verification
        self.assertEqual(
            [event for event, _ in self.audit_events],
            [
                "code_execution.started",
                "code_execution.tool_requested",
                "code_execution.tool_completed",
                "code_execution.completed",
            ],
        )

    def test_untrusted_or_revoked_grant_never_reaches_host_dispatch(self):
        for revoke_on_call in (1, 2):
            with self.subTest(revoke_on_call=revoke_on_call):
                verifier = _Verifier(revoke_on_call=revoke_on_call)
                executor = _Executor()
                dispatched = []
                service = CodeExecutionService(executor, verifier, audit=self._audit)
                with self.assertRaises(CodeExecutionContractError):
                    service.execute(
                        self.request,
                        self.grant,
                        dispatch_tool=lambda name, args: dispatched.append(name) or {"content": "x"},
                        cancellation_requested=lambda: False,
                    )
                self.assertEqual(dispatched, [])
                if revoke_on_call == 1:
                    self.assertEqual(executor.calls, 0)

    def test_tool_outside_manifest_is_rejected(self):
        verifier = _Verifier()
        executor = _Executor(tool_name="workspace.write")
        service = CodeExecutionService(executor, verifier, audit=self._audit)
        with self.assertRaisesRegex(CodeExecutionContractError, "manifest"):
            service.execute(
                self.request,
                self.grant,
                dispatch_tool=lambda name, args: {"content": "should not run"},
                cancellation_requested=lambda: False,
            )

    def test_executor_cannot_forge_host_tool_call_count(self):
        class LyingExecutor:
            def execute(self, request, grant, *, dispatch_tool, cancellation_requested):
                dispatch_tool("workspace.read", {"path": "README.md"})
                return CodeExecutionResult("succeeded", "safe", 0, 0.1)

        service = CodeExecutionService(LyingExecutor(), _Verifier(), audit=self._audit)
        with self.assertRaisesRegex(CodeExecutionContractError, "does not match"):
            service.execute(
                self.request,
                self.grant,
                dispatch_tool=lambda name, args: {"content": "safe"},
                cancellation_requested=lambda: False,
            )

    def test_audit_failure_prevents_backend_execution(self):
        executor = _Executor()

        def failing_audit(event, metadata):
            raise OSError("audit store unavailable")

        service = CodeExecutionService(executor, _Verifier(), audit=failing_audit)
        with self.assertRaisesRegex(CodeExecutionContractError, "audit"):
            service.execute(
                self.request,
                self.grant,
                dispatch_tool=lambda name, args: {"content": "never"},
                cancellation_requested=lambda: False,
            )
        self.assertEqual(executor.calls, 0)

    def test_cancelled_or_unknown_cancellation_state_blocks_tool_rpc(self):
        for callback in (lambda: True, lambda: None, lambda: (_ for _ in ()).throw(OSError())):
            with self.subTest(callback=callback):
                service = CodeExecutionService(_Executor(), _Verifier(), audit=self._audit)
                with self.assertRaises(CodeExecutionContractError):
                    service.execute(
                        self.request,
                        self.grant,
                        dispatch_tool=lambda name, args: {"content": "never"},
                        cancellation_requested=callback,
                    )

    def test_oversized_host_tool_arguments_are_rejected(self):
        class OversizedExecutor:
            def execute(self, request, grant, *, dispatch_tool, cancellation_requested):
                dispatch_tool("workspace.read", {"payload": "x" * 40_000})
                return CodeExecutionResult("succeeded", "", 1, 0.1)

        service = CodeExecutionService(OversizedExecutor(), _Verifier(), audit=self._audit)
        with self.assertRaisesRegex(CodeExecutionContractError, "arguments"):
            service.execute(
                self.request,
                self.grant,
                dispatch_tool=lambda name, args: {"content": "never"},
                cancellation_requested=lambda: False,
            )


if __name__ == "__main__":
    unittest.main()
