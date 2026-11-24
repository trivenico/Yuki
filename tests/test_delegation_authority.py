import tempfile
import time
import unittest
from pathlib import Path

from core.delegation_authority import DelegationAuthority


class DelegationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.authority = DelegationAuthority(str(Path(self.tmp.name) / "delegations.sqlite3"))
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def grant(self, **overrides):
        values = dict(
            grantor_id="human:owner", grantee_id="workload:agent-a",
            tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
            scopes={"task:progress", "task:complete"},
            expires_at=self.now + 300, now=self.now,
        )
        values.update(overrides)
        return self.authority.create_grant(**values)

    def test_exact_task_scope_and_identity_are_required(self):
        grant = self.grant()
        verified = self.authority.verify(
            grant.delegation_id, grantee_id="workload:agent-a",
            tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
            scopes={"task:progress"}, now=self.now + 1,
        )
        self.assertEqual(verified.delegation_id, grant.delegation_id)
        for context in (
            {"grantee_id": "workload:other"},
            {"tenant_id": "tenant-b"},
            {"agent_id": "agent-b"},
            {"task_id": "task-b"},
            {"scopes": {"task:delete"}},
        ):
            args = dict(grantee_id="workload:agent-a", tenant_id="tenant-a",
                        agent_id="agent-a", task_id="task-a", scopes={"task:progress"},
                        now=self.now + 1)
            args.update(context)
            with self.assertRaises(PermissionError):
                self.authority.verify(grant.delegation_id, **args)

    def test_child_grant_cannot_escalate_parent_scope_or_expiry(self):
        parent = self.grant()
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id="workload:agent-a", grantee_id="workload:child",
                scopes={"task:delete"}, parent_id=parent.delegation_id,
            )
        child = self.grant(
            grantor_id="workload:agent-a", grantee_id="workload:child",
            scopes={"task:progress"}, expires_at=self.now + 200,
            parent_id=parent.delegation_id,
        )
        self.assertEqual(child.parent_id, parent.delegation_id)
        self.authority.verify(
            child.delegation_id, grantee_id="workload:child",
            tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
            scopes={"task:progress"}, now=self.now + 1,
        )

    def test_parent_revocation_invalidates_descendant(self):
        parent = self.grant()
        child = self.grant(
            grantor_id="workload:agent-a", grantee_id="workload:child",
            scopes={"task:progress"}, expires_at=self.now + 200,
            parent_id=parent.delegation_id,
        )
        self.assertTrue(self.authority.revoke(parent.delegation_id, revoked_by="human:owner", now=self.now + 2))
        with self.assertRaises(PermissionError):
            self.authority.verify(
                child.delegation_id, grantee_id="workload:child",
                tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
                scopes={"task:progress"}, now=self.now + 3,
            )

    def test_expired_and_unauthorized_revocation_fail_closed(self):
        grant = self.grant(expires_at=self.now + 1)
        with self.assertRaises(PermissionError):
            self.authority.verify(
                grant.delegation_id, grantee_id="workload:agent-a",
                tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
                scopes={"task:progress"}, now=self.now + 1,
            )
        with self.assertRaises(PermissionError):
            self.authority.revoke(grant.delegation_id, revoked_by="unrelated", now=self.now + 1)


    def test_issue_verify_and_revoke_are_durably_audited(self):
        grant = self.grant()
        self.authority.verify(
            grant.delegation_id, grantee_id="workload:agent-a",
            tenant_id="tenant-a", agent_id="agent-a", task_id="task-a",
            scopes={"task:progress"}, now=self.now + 1,
        )
        self.authority.revoke(grant.delegation_id, revoked_by="human:owner", now=self.now + 2)
        with self.authority._connect() as db:
            events = db.execute(
                "SELECT event_type, actor_id FROM workload_delegation_events "
                "WHERE delegation_id=? ORDER BY event_id",
                (grant.delegation_id,),
            ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["actor_id"]) for row in events],
            [("issued", "human:owner"), ("verified", "workload:agent-a"), ("revoked", "human:owner")],
        )


    def test_delegation_chain_depth_is_bounded(self):
        parent = self.grant()
        for index in range(1, 8):
            parent = self.grant(
                grantor_id=parent.grantee_id,
                grantee_id=f"workload:child-{index}",
                scopes={"task:progress"}, expires_at=self.now + 300 - index,
                parent_id=parent.delegation_id,
            )
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id=parent.grantee_id, grantee_id="workload:too-deep",
                scopes={"task:progress"}, expires_at=self.now + 100,
                parent_id=parent.delegation_id,
            )


if __name__ == "__main__":
    unittest.main()
