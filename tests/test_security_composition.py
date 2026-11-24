import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from providers.authorized_text_provider import AuthorizedTextProvider
from core.security_composition import (
    PrincipalPermissionPolicy,
    SQLiteAuthorizationAuditLog,
    build_authorization_security_runtime,
    require_supported_entrypoint,
)


class SecurityCompositionTests(unittest.TestCase):

    def test_strict_deployment_blocks_legacy_entrypoints(self):
        with patch.dict(os.environ, {"YUKI_REQUIRE_CENTRAL_AUTHORIZATION": "true"}):
            with self.assertRaisesRegex(RuntimeError, "central_authorization_not_composed"):
                require_supported_entrypoint("main.py")
            with self.assertRaisesRegex(RuntimeError, "central_authorization_not_composed"):
                require_supported_entrypoint("yuki.py")

    def test_legacy_entrypoint_guard_is_inactive_without_strict_deployment_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            require_supported_entrypoint("main.py")

    def test_permission_policy_requires_exact_explicit_action(self):
        policy = PrincipalPermissionPolicy("policy-v1")
        allowed = policy.authorize(AuthorizationRequest(
            Principal("owner-1", "human", permissions=frozenset({"inference:invoke"})),
            "inference:invoke", "inference:sha256:abc", risk="high",
        ))
        denied = policy.authorize(AuthorizationRequest(
            Principal("owner-1", "human", permissions=frozenset({"inference:invoke"})),
            "inference:provider:select", "provider:ollama", risk="high",
        ))
        self.assertIs(allowed.allowed, True)
        self.assertIs(denied.allowed, False)
        self.assertEqual(allowed.policy_revision, "policy-v1")

    def test_audit_log_is_durable_append_only_and_hash_chained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security" / "authorization.sqlite3"
            security = build_authorization_security_runtime(path, policy_revision="policy-v1")
            principal = Principal(
                "owner-1", "human", permissions=frozenset({"inference:invoke"}),
            )
            request = AuthorizationRequest(
                principal, "inference:invoke", "inference:sha256:private-digest",
                risk="high", expected_policy_revision="policy-v1",
            )
            security.gateway.require(request)
            security.gateway.require(request)
            self.assertTrue(security.audit_log.verify_chain())
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM authorization_audit_events"
                ).fetchone()[0]
                self.assertEqual(count, 2)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE authorization_audit_events SET reason_code='tampered' WHERE sequence=1"
                    )
                connection.rollback()
            finally:
                connection.close()
            self.assertTrue(security.audit_log.verify_chain())

    def test_audit_chain_detects_out_of_band_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorization.sqlite3"
            security = build_authorization_security_runtime(path, policy_revision="policy-v1")
            request = AuthorizationRequest(
                Principal("owner-1", "human", permissions=frozenset({"inference:invoke"})),
                "inference:invoke", "inference:sha256:abc",
            )
            security.gateway.require(request)
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TRIGGER authorization_audit_no_update")
                connection.execute(
                    "UPDATE authorization_audit_events SET resource='tampered' WHERE sequence=1"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertFalse(security.audit_log.verify_chain())


    def test_text_provider_authorizes_and_hashes_prompt_before_dispatch(self):
        class Provider:
            def __init__(self):
                self.prompts = []
            def is_available(self):
                return True
            def generate(self, prompt):
                self.prompts.append(prompt)
                return "ok"

        with tempfile.TemporaryDirectory() as directory:
            security = build_authorization_security_runtime(
                Path(directory) / "authorization.sqlite3", policy_revision="policy-v1",
            )
            provider = Provider()
            wrapped = AuthorizedTextProvider(
                provider, authorization_gateway=security.gateway,
                principal=Principal("local-cli", "human", permissions=frozenset({"inference:invoke"})),
                provider_name="test-provider", policy_revision="policy-v1",
            )
            self.assertEqual(wrapped.generate("sensitive prompt"), "ok")
            self.assertEqual(provider.prompts, ["sensitive prompt"])
            connection = sqlite3.connect(Path(directory) / "authorization.sqlite3")
            try:
                row = connection.execute(
                    "SELECT action, resource FROM authorization_audit_events"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "inference:invoke")
            self.assertNotIn("sensitive prompt", row[1])

    def test_text_provider_denies_cloud_hybrid_route_without_cloud_permission(self):
        class Hybrid:
            cloud_tasks = frozenset({"research"})
            def is_available(self):
                return True
            def generate(self, prompt):
                raise AssertionError("provider must not be invoked")
            def generate_for(self, task, prompt):
                raise AssertionError("cloud route must not be invoked")

        with tempfile.TemporaryDirectory() as directory:
            security = build_authorization_security_runtime(
                Path(directory) / "authorization.sqlite3", policy_revision="policy-v1",
            )
            wrapped = AuthorizedTextProvider(
                Hybrid(), authorization_gateway=security.gateway,
                principal=Principal("local-cli", "human", permissions=frozenset({"inference:invoke"})),
                provider_name="hybrid", policy_revision="policy-v1",
            )
            with self.assertRaises(PermissionError):
                wrapped.generate_for("research", "sensitive prompt")
            connection = sqlite3.connect(Path(directory) / "authorization.sqlite3")
            try:
                action = connection.execute(
                    "SELECT action FROM authorization_audit_events"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(action, "inference:cloud:invoke")

    def test_gateway_denies_when_audit_database_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorization.sqlite3"
            security = build_authorization_security_runtime(path, policy_revision="policy-v1")
            path.unlink()
            Path(str(path) + "-wal").unlink(missing_ok=True)
            Path(str(path) + "-shm").unlink(missing_ok=True)
            path.parent.rmdir()
            request = AuthorizationRequest(
                Principal("owner-1", "human", permissions=frozenset({"inference:invoke"})),
                "inference:invoke", "inference:sha256:abc",
            )
            with self.assertRaises(PermissionError):
                security.gateway.require(request)


if __name__ == "__main__":
    unittest.main()
