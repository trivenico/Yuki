"""Tests for per-owner durable approval runtime composition."""
import hashlib
import tempfile
import unittest
from pathlib import Path

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

    def test_executor_resolution_is_bound_to_coordinator_owner_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            resolutions = []
            factory = OwnerApprovalRuntimeFactory(
                directory,
                planner_resolver_for_owner=lambda owner, session: resolutions.append((owner, session)) or "executor",
            )
            coordinator = factory.coordinator_for_owner("owner:alice")
            self.assertEqual(
                coordinator.planner_resolver(actor="owner:alice", session_id="session-a"),
                "executor",
            )
            self.assertIsNone(
                coordinator.planner_resolver(actor="owner:bob", session_id="session-b"),
            )
            self.assertEqual(resolutions, [("owner:alice", "session-a")])

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


if __name__ == "__main__":
    unittest.main()
