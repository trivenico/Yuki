"""Tests for the durable, revision-fenced MCP tool policy store."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from core.mcp_tool_policy import MCPToolPolicy, MCPToolRisk
from core.mcp_tool_policy_store import (
    MCPToolPolicyConflict,
    MCPToolPolicyStoreError,
    SQLiteMCPToolPolicyStore,
)


class SQLiteMCPToolPolicyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "mcp-policies.sqlite3")
        self.store = SQLiteMCPToolPolicyStore(self.path)

    def policy(self, tool="read_profile", *, owner="owner", provider="provider",
               account="account", risk=MCPToolRisk.READ_ONLY, scopes=("profile:read",),
               version=1, enabled=True):
        return MCPToolPolicy(
            owner_id=owner, provider_id=provider, account_id=account,
            tool_name=tool, risk=risk, required_scopes=tuple(scopes),
            version=version, enabled=enabled,
        )

    def test_empty_scope_has_stable_initial_revision(self):
        self.assertEqual(self.store.current_revision(
            owner_id="owner", provider_id="provider", account_id="account"
        ), 1)
        snapshot = self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.policy_count, 0)

    def test_policy_and_revision_survive_store_restart(self):
        policy = self.policy()
        revision = self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=policy, expected_revision=1,
        )
        reopened = SQLiteMCPToolPolicyStore(self.path)
        snapshot = reopened.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        self.assertEqual(revision, 2)
        self.assertEqual(snapshot.revision, 2)
        self.assertEqual(snapshot.get(
            provider_id="provider", account_id="account", tool_name="read_profile"
        ), policy)
        self.assertEqual(snapshot.tool_names(provider_id="provider", account_id="account"),
                         ("read_profile",))

    def test_policy_version_and_registry_revision_advance_independently(self):
        first = self.policy()
        self.assertEqual(self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=first, expected_revision=1,
        ), 2)
        updated = self.policy(version=2, risk=MCPToolRisk.WRITE,
                              scopes=("profile:write",))
        self.assertEqual(self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=updated, expected_revision=2,
        ), 3)
        snapshot = self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        self.assertEqual(snapshot.revision, 3)
        self.assertEqual(snapshot.get(
            provider_id="provider", account_id="account", tool_name="read_profile"
        ).risk, MCPToolRisk.WRITE)

    def test_stale_compare_and_swap_is_rejected_without_partial_write(self):
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self.policy(), expected_revision=1,
        )
        with self.assertRaises(MCPToolPolicyConflict):
            self.store.set_policy(
                owner_id="owner", provider_id="provider", account_id="account",
                policy=self.policy("write_profile"), expected_revision=1,
            )
        snapshot = self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        self.assertEqual(snapshot.revision, 2)
        self.assertEqual(snapshot.tool_names(provider_id="provider", account_id="account"),
                         ("read_profile",))

    def test_concurrent_writers_with_same_revision_have_one_winner(self):
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def write(tool):
            barrier.wait()
            try:
                result = self.store.set_policy(
                    owner_id="owner", provider_id="provider", account_id="account",
                    policy=self.policy(tool), expected_revision=1,
                )
                value = ("ok", result)
            except MCPToolPolicyConflict:
                value = ("conflict", None)
            with lock:
                outcomes.append(value)

        threads = [
            threading.Thread(target=write, args=("read_profile",)),
            threading.Thread(target=write, args=("write_profile",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(item[0] for item in outcomes), ["conflict", "ok"])
        snapshot = self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        self.assertEqual(snapshot.revision, 2)
        self.assertEqual(snapshot.policy_count, 1)

    def test_remove_advances_revision_and_keeps_tombstone_revision(self):
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self.policy(), expected_revision=1,
        )
        self.assertEqual(self.store.remove_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            tool_name="read_profile", expected_revision=2,
        ), 3)
        self.assertEqual(self.store.current_revision(
            owner_id="owner", provider_id="provider", account_id="account"
        ), 3)
        self.assertEqual(self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        ).policy_count, 0)
        with self.assertRaises(MCPToolPolicyConflict):
            self.store.set_policy(
                owner_id="owner", provider_id="provider", account_id="account",
                policy=self.policy(), expected_revision=2,
            )

    def test_revocation_immediately_invalidates_existing_approver_snapshot(self):
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self.policy(), expected_revision=1,
        )
        snapshot = self.store.load_registry(
            owner_id="owner", provider_id="provider", account_id="account"
        )
        events = []
        approve = snapshot.build_approver(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",), owner_approval=lambda _request: True,
            audit=events.append,
            current_revision=lambda: self.store.current_revision(
                owner_id="owner", provider_id="provider", account_id="account"
            ),
        )
        self.assertTrue(approve("read_profile", {}))
        self.store.remove_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            tool_name="read_profile", expected_revision=2,
        )
        self.assertFalse(approve("read_profile", {}))
        self.assertEqual([event["event"] for event in events],
                         ["requested", "authorized_read_only"])

    def test_owner_provider_and_account_scopes_are_isolated(self):
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self.policy(), expected_revision=1,
        )
        self.store.set_policy(
            owner_id="other-owner", provider_id="provider", account_id="account",
            policy=self.policy(owner="other-owner"), expected_revision=1,
        )
        self.store.set_policy(
            owner_id="owner", provider_id="other-provider", account_id="account",
            policy=self.policy(provider="other-provider"), expected_revision=1,
        )
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="other-account",
            policy=self.policy(account="other-account"), expected_revision=1,
        )
        for owner, provider, account in (
            ("owner", "provider", "account"),
            ("other-owner", "provider", "account"),
            ("owner", "other-provider", "account"),
            ("owner", "provider", "other-account"),
        ):
            snapshot = self.store.load_registry(
                owner_id=owner, provider_id=provider, account_id=account
            )
            self.assertEqual(snapshot.policy_count, 1)
            self.assertEqual(snapshot.revision, 2)

    def test_scope_mismatch_and_invalid_revision_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "scope_mismatch"):
            self.store.set_policy(
                owner_id="owner", provider_id="provider", account_id="account",
                policy=self.policy(owner="someone-else"), expected_revision=1,
            )
        for invalid in (True, 0, -1, 1.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.store.set_policy(
                        owner_id="owner", provider_id="provider", account_id="account",
                        policy=self.policy(), expected_revision=invalid,
                    )

    def test_corrupt_persisted_policy_fails_closed(self):
        self.store.set_policy(
            owner_id="owner", provider_id="provider", account_id="account",
            policy=self.policy(), expected_revision=1,
        )
        db = sqlite3.connect(self.path)
        try:
            db.execute("""UPDATE mcp_tool_policies SET required_scopes_json='not-json'
                WHERE owner_id='owner' AND provider_id='provider' AND account_id='account'""")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(MCPToolPolicyStoreError, "stored_policy_corrupt"):
            self.store.load_registry(
                owner_id="owner", provider_id="provider", account_id="account"
            )


if __name__ == "__main__":
    unittest.main()
