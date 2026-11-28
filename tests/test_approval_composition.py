"""Tests for the trusted approval composition root."""
import tempfile
import unittest
from pathlib import Path

from core.approval_composition import (
    ApprovalCompositionError,
    build_approval_services,
)
from core.auth import BuiltInAuthService
from core.maintenance_fence import MaintenanceFence


class ApprovalCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fence = MaintenanceFence(self.root)
        self.auth = BuiltInAuthService(
            str(self.root / "auth.sqlite3"),
            password_iterations=100_000,
            maintenance_fence=self.fence,
        )
        self.auth.bootstrap_admin("owner@example.test", "correct horse battery staple")
        self.session = self.auth.authenticate(
            "owner@example.test", "correct horse battery staple"
        )
        self.handlers = {"notes.write": lambda argument: "saved"}
        self.scopes = {"notes.write": "owner-notes"}

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **overrides):
        values = {
            "auth_service": self.auth,
            "profile_root": self.root,
            "maintenance_fence": self.fence,
            "session_token_provider": lambda: self.session.token,
            "handlers": self.handlers,
            "target_scopes": self.scopes,
        }
        values.update(overrides)
        return build_approval_services(**values)

    def test_all_stores_and_services_share_one_profile_fence(self):
        services = self.build()
        self.assertIs(services.pending.maintenance_fence, self.fence)
        self.assertIs(services.audit.maintenance_fence, self.fence)
        self.assertIs(services.grants.maintenance_fence, self.fence)
        self.assertIs(services.coordinator.maintenance_fence, self.fence)
        self.assertEqual(services.profile_root, self.root.resolve())
        for store in (services.pending, services.audit, services.grants):
            self.assertEqual(Path(store.database_path).resolve().parent, self.root.resolve())

    def test_rejects_auth_service_with_different_fence(self):
        other = MaintenanceFence(self.root)
        with self.assertRaisesRegex(ApprovalCompositionError, "share one maintenance fence"):
            self.build(maintenance_fence=other)

    def test_rejects_fence_root_mismatch(self):
        other_root = self.root / "other"
        other_root.mkdir()
        other_fence = MaintenanceFence(other_root)
        with self.assertRaisesRegex(ApprovalCompositionError, "do not match"):
            self.build(profile_root=other_root, maintenance_fence=other_fence)

    def test_rejects_missing_or_mismatched_handler_scopes(self):
        with self.assertRaisesRegex(ValueError, "non-empty handler"):
            self.build(handlers={})
        with self.assertRaisesRegex(ValueError, "exactly one target scope"):
            self.build(target_scopes={})

    def test_gateway_is_session_bound_and_can_record_without_dispatch(self):
        services = self.build()
        result = services.gateway.request(
            object(),
            {"action": {"skill_id": "notes.write", "arguments": {"text": "draft"}}},
        )
        self.assertEqual(result.status, "pending_approval")
        self.assertEqual(services.audit.list_events(limit=10)[0]["event_type"], "requested")


if __name__ == "__main__":
    unittest.main()
