"""Tests for per-owner durable approval runtime composition."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from core.guarded_tool_runtime import GuardedToolRuntime
from providers.owner_approval_factory import OwnerApprovalRuntimeFactory


class OwnerApprovalRuntimeFactoryTests(unittest.TestCase):
    def test_factory_isolates_owner_storage_and_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = OwnerApprovalRuntimeFactory(
                directory,
                planner_resolver_for_owner=lambda owner, session: None,
            )
            alice = factory.adapter_for_owner("owner:alice")
            bob = factory.adapter_for_owner("owner:bob")
            self.assertIs(alice, factory.adapter_for_owner("owner:alice"))
            self.assertIsNot(alice, bob)

            alice_coordinator = factory.coordinator_for_owner("owner:alice")
            bob_coordinator = factory.coordinator_for_owner("owner:bob")
            self.assertIsNot(alice_coordinator.pending_store, bob_coordinator.pending_store)
            self.assertIsNot(alice_coordinator.audit_log, bob_coordinator.audit_log)

            root = Path(directory)
            alice_dir = root / hashlib.sha256(b"owner:alice").hexdigest()
            bob_dir = root / hashlib.sha256(b"owner:bob").hexdigest()
            for owner_dir in (alice_dir, bob_dir):
                self.assertTrue((owner_dir / "pending.sqlite3").is_file())
                self.assertTrue((owner_dir / "audit.sqlite3").is_file())
            self.assertNotEqual(alice_dir, bob_dir)

    def test_executor_resolution_requires_guarded_runtime_and_binds_owner_session(self):
        with tempfile.TemporaryDirectory() as directory:
            resolutions = []
            class Policy:
                def authorize_tool(self, name):
                    return False
            guarded = GuardedToolRuntime(
                {}, Policy(), audit=lambda *_: None, require_audit=True,
            )
            factory = OwnerApprovalRuntimeFactory(
                directory,
                planner_resolver_for_owner=lambda owner, session: resolutions.append((owner, session)) or guarded,
            )
            coordinator = factory.coordinator_for_owner("owner:alice")
            executor = coordinator.planner_resolver(actor="owner:alice", session_id="session-a")
            self.assertTrue(callable(getattr(executor, "execute_approved_action", None)))
            self.assertIsNone(
                coordinator.planner_resolver(actor="owner:bob", session_id="session-b"),
            )
            self.assertEqual(resolutions, [("owner:alice", "session-a")])

    def test_executor_resolution_rejects_arbitrary_executor_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = OwnerApprovalRuntimeFactory(
                directory, planner_resolver_for_owner=lambda *_: object(),
            )
            coordinator = factory.coordinator_for_owner("owner:alice")
            self.assertIsNone(
                coordinator.planner_resolver(actor="owner:alice", session_id="session-a"),
            )

    def test_rejects_invalid_owner_and_symlink_storage_root(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = OwnerApprovalRuntimeFactory(
                directory, planner_resolver_for_owner=lambda owner, session: None,
            )
            with self.assertRaises(ValueError):
                factory.adapter_for_owner("")
            link = Path(directory) / "link"
            target = Path(directory) / "target"
            target.mkdir()
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                OwnerApprovalRuntimeFactory(
                    link, planner_resolver_for_owner=lambda owner, session: None,
                )


class OwnerApprovalProviderBindingTests(unittest.TestCase):
    def test_provider_binder_is_owner_bound_and_persists_exact_identity(self):
        from core.operation_context import OperationExecutionContext
        with tempfile.TemporaryDirectory() as tmp:
            factory = OwnerApprovalRuntimeFactory(
                tmp, planner_resolver_for_owner=lambda _owner, _session: None,
            )
            coordinator = factory.coordinator_for_owner("owner-a")
            store = coordinator.pending_store
            item = store.put(
                "payments.create", {"amount": 10},
                {"skill_id": "payments.create", "arguments": {"amount": 10}},
                actor="owner-a", session_id="session-a",
            )
            store.transition(item.pending_id, expected_status="pending", new_status="claimed",
                             actor="owner-a", session_id="session-a")
            store.transition(item.pending_id, expected_status="claimed", new_status="approved",
                             actor="owner-a", session_id="session-a")
            intent = store.ensure_operation_intent(item.pending_id, actor="owner-a", session_id="session-a")
            store.transition(item.pending_id, expected_status="approved", new_status="executing",
                             actor="owner-a", session_id="session-a")
            context = OperationExecutionContext(
                pending_id=item.pending_id, operation_id=intent["operation_id"],
                action_digest=intent["action_digest"], actor="owner-a", session_id="session-a",
            )
            binder = factory.provider_binder_for_owner("owner-a")
            binder(context, "acme_payments")
            self.assertEqual(
                store.get_operation_intent(item.pending_id, actor="owner-a", session_id="session-a")["provider_id"],
                "acme_payments",
            )
            with self.assertRaises(PermissionError):
                factory.bind_provider_for_owner("owner-b", context, "acme_payments")


if __name__ == "__main__":
    unittest.main()
