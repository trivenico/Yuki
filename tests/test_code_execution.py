"""Contract tests for provider-neutral generated-code execution."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from core.code_execution import (
    CodeExecutionContractError,
    CodeExecutionManifest,
    CodeExecutionRequest,
    CodeExecutionResult,
    ExecutionGrant,
)


class CodeExecutionContractTests(unittest.TestCase):
    def setUp(self):
        self.request = CodeExecutionRequest(
            execution_id="exec-01",
            actor_id="actor-01",
            session_id="session-01",
            task_id="task-01",
            source="result = 1 + 1",
            tool_names=("workspace.read",),
            timeout_seconds=20,
            max_tool_calls=3,
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
            token="opaque-signed-token",
        )

    def test_request_manifest_binds_every_execution_field(self):
        digest = self.request.manifest_sha256
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, replace(self.request, source="result = 3").manifest_sha256)
        self.assertNotEqual(digest, replace(self.request, timeout_seconds=21).manifest_sha256)
        self.assertNotEqual(digest, replace(self.request, tool_names=()).manifest_sha256)
        self.assertNotEqual(digest, replace(self.request, actor_id="actor-02").manifest_sha256)

    def test_source_free_manifest_preserves_exact_request_binding(self):
        manifest = self.request.manifest
        self.assertIsInstance(manifest, CodeExecutionManifest)
        self.assertEqual(manifest.source_sha256, self.request.source_sha256)
        self.assertEqual(manifest.manifest_sha256, self.request.manifest_sha256)
        self.grant.validate_for(manifest)
        altered = replace(manifest, max_tool_calls=manifest.max_tool_calls - 1)
        with self.assertRaises(CodeExecutionContractError):
            self.grant.validate_for(altered)

    def test_default_network_policy_is_disabled(self):
        self.assertFalse(self.request.validate().network_access)
        with self.assertRaisesRegex(CodeExecutionContractError, "network access"):
            replace(self.request, network_access=True).validate()

    def test_request_rejects_oversized_source_and_resource_budgets(self):
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, source="x" * 65_537).validate()
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, source="invalid\x00source").validate()
        self.assertIsNotNone(
            replace(self.request, source=r"literal\x00text").validate()
        )
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, timeout_seconds=301).validate()
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, max_result_bytes=1_048_577).validate()

    def test_request_rejects_invalid_and_duplicate_tool_names(self):
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, tool_names=("bad name",)).validate()
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, tool_names=("workspace.read", "workspace.read")).validate()
        with self.assertRaises(CodeExecutionContractError):
            replace(self.request, tool_names=([],)).validate()

    def test_grant_must_match_exact_actor_session_task_and_manifest(self):
        self.grant.validate_for(self.request)
        for field, value in (
            ("actor_id", "actor-02"),
            ("session_id", "session-02"),
            ("task_id", "task-02"),
            ("execution_id", "exec-02"),
            ("manifest_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                with self.assertRaises(CodeExecutionContractError):
                    replace(self.grant, **{field: value}).validate_for(self.request)

    def test_grant_rejects_expired_and_timezone_naive_expiry(self):
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(CodeExecutionContractError, "expired"):
            replace(self.grant, expires_at=expired).validate_for(self.request)
        with self.assertRaisesRegex(CodeExecutionContractError, "timezone"):
            replace(self.grant, expires_at="2035-01-01T00:00:00").validate_for(self.request)

    def test_grant_only_allows_requested_registered_tools(self):
        with self.assertRaisesRegex(CodeExecutionContractError, "exactly match"):
            replace(self.grant, allowed_tools=()).validate_for(self.request)
        with self.assertRaisesRegex(CodeExecutionContractError, "exactly match"):
            replace(self.grant, allowed_tools=("workspace.read", "workspace.write")).validate_for(self.request)
        with self.assertRaises(CodeExecutionContractError):
            replace(self.grant, allowed_tools=("workspace.read", "workspace.read")).validate_for(self.request)

    def test_grant_token_is_not_exposed_by_repr(self):
        self.assertNotIn("opaque-signed-token", repr(self.grant))

    def test_result_enforces_output_tool_call_and_duration_limits(self):
        result = CodeExecutionResult("succeeded", "ok", 1, 1.25).validate(self.request)
        self.assertEqual(result.status, "succeeded")
        for invalid in (
            CodeExecutionResult("succeeded", "x" * 129, 0, 1),
            CodeExecutionResult("succeeded", "", 4, 1),
            CodeExecutionResult("succeeded", "", 0, float("inf")),
            CodeExecutionResult("succeeded", "", 0, float("nan")),
            CodeExecutionResult("succeeded", "", 0, 10**1000),
            CodeExecutionResult("unknown", "", 0, 1),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CodeExecutionContractError):
                    invalid.validate(self.request)

    def test_result_rejects_non_boolean_truncation_flag(self):
        with self.assertRaises(CodeExecutionContractError):
            CodeExecutionResult("failed", "", 0, 1, 1).validate(self.request)


if __name__ == "__main__":
    unittest.main()
