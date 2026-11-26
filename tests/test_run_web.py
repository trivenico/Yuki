import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.authorization import Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.production_security import ProductionSecurityContext
from scripts.run_web import create_app


class AllowPolicy:
    def authorize(self, request):
        from core.authorization import AuthorizationDecision
        return AuthorizationDecision(True, "decision-1", "revision-1", "allowed")


class MemoryAnchor:
    def __init__(self):
        self.checkpoint = None
    def publish(self, checkpoint):
        self.checkpoint = checkpoint
    def latest(self):
        return self.checkpoint


def security_context():
    audit_log = SQLiteAuthorizationAuditLog(str(Path(tempfile.mkdtemp()) / "authorization-audit.sqlite3"))
    anchor = MemoryAnchor()
    audit_log.publish_checkpoint(anchor)
    return ProductionSecurityContext(
        authorization_gateway=AuthorizationGateway(AllowPolicy(), audit=audit_log),
        audit_anchor=anchor,
        principal_for_actor=lambda owner: Principal(owner, "human", tenant_id="tenant-1"),
        principal_for_worker=lambda worker: Principal(
            worker, "workload", tenant_id="tenant-1", agent_id="worker-1",
        ),
        policy_revision="revision-1",
    )


class WebProductionBootstrapTests(unittest.TestCase):
    def test_production_environment_fails_before_config_or_provider_initialization(self):
        with patch.dict(os.environ, {"YUKI_ENVIRONMENT": "production"}, clear=True):
            with patch("scripts.run_web.load_config") as load_config:
                with self.assertRaisesRegex(
                    RuntimeError, "production_web_requires_owned_security_runtime"
                ):
                    create_app()
                load_config.assert_not_called()

    def test_public_bind_fails_closed_without_security_context(self):
        with patch.dict(os.environ, {
            "YUKI_WEB_HOST": "0.0.0.0",
            "YUKI_WEB_ALLOW_PUBLIC_BIND": "true",
        }, clear=True):
            with patch("scripts.run_web.load_config") as load_config:
                with self.assertRaisesRegex(
                    RuntimeError, "production_web_requires_owned_security_runtime"
                ):
                    create_app()
                load_config.assert_not_called()

    def test_non_production_loopback_does_not_require_production_context(self):
        with patch.dict(os.environ, {"YUKI_WEB_HOST": "127.0.0.1"}, clear=True):
            # The local path proceeds to config loading; avoid constructing its dependencies.
            with patch("scripts.run_web.load_config", side_effect=RuntimeError("config reached")) as load_config:
                with self.assertRaisesRegex(RuntimeError, "config reached"):
                    create_app()
                load_config.assert_called_once()

    def test_production_context_alone_is_not_a_complete_bootstrap(self):
        with patch.dict(os.environ, {"YUKI_ENVIRONMENT": "production"}, clear=True):
            with patch("scripts.run_web.load_config") as load_config:
                with self.assertRaisesRegex(RuntimeError, "production_web_requires_owned_security_runtime"):
                    create_app(production_security=security_context())
                load_config.assert_not_called()

    def test_invalid_security_context_type_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TypeError, "ProductionSecurityContext"):
                create_app(production_security=object())


    def test_production_security_runtime_supplies_its_context_to_web_bootstrap(self):
        with patch.dict(os.environ, {"YUKI_ENVIRONMENT": "production"}, clear=True):
            security = security_context()
            class Runtime:
                def __init__(self):
                    self.security = security
            runtime = Runtime()
            with patch("scripts.run_web.load_config", side_effect=RuntimeError("config reached")) as load_config:
                with self.assertRaisesRegex(RuntimeError, "config reached"):
                    create_app(production_security_runtime=runtime)
                load_config.assert_called_once()

    def test_production_security_runtime_rejects_mismatched_context(self):
        with patch.dict(os.environ, {"YUKI_ENVIRONMENT": "production"}, clear=True):
            class Runtime:
                security = security_context()
            with self.assertRaisesRegex(ValueError, "production_security_runtime_context_mismatch"):
                create_app(
                    production_security=security_context(),
                    production_security_runtime=Runtime(),
                )


if __name__ == "__main__":
    unittest.main()
