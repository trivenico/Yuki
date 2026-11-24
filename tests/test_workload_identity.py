import unittest

from core.workload_identity import (
    WorkloadCredentialClaims,
    WorkloadSigningKey,
)


class WorkloadIdentityContractTests(unittest.TestCase):
    def claims(self, **overrides):
        values = {
            "issuer": "https://issuer.yuki.internal",
            "subject": "workload:tenant-a:agent-1",
            "tenant_id": "tenant-a",
            "agent_id": "agent-1",
            "audience": "yuki-task-runtime",
            "issued_at": 1000,
            "not_before": 1000,
            "expires_at": 1300,
            "token_id": "jti-unique-1",
            "task_id": "task-1",
            "lease_generation": "lease-generation-4",
            "scopes": frozenset({"task:progress", "tool:invoke"}),
        }
        values.update(overrides)
        return WorkloadCredentialClaims(**values)

    def test_valid_claims_are_immutable_and_bounded(self):
        claims = self.claims()
        self.assertEqual(claims.task_id, "task-1")
        with self.assertRaises(AttributeError):
            claims.task_id = "task-2"

    def test_rejects_boolean_timestamps(self):
        with self.assertRaises(ValueError):
            self.claims(issued_at=True)

    def test_rejects_inconsistent_time_window(self):
        with self.assertRaises(ValueError):
            self.claims(issued_at=1100, not_before=1000)

    def test_rejects_excessive_lifetime(self):
        with self.assertRaises(ValueError):
            self.claims(expires_at=2000)

    def test_rejects_empty_scopes(self):
        with self.assertRaises(ValueError):
            self.claims(scopes=frozenset())

    def test_rejects_mutable_scope_collection(self):
        with self.assertRaises(ValueError):
            self.claims(scopes={"task:progress"})

    def test_rejects_unknown_signing_algorithm(self):
        with self.assertRaises(ValueError):
            WorkloadSigningKey(
                issuer="issuer", key_id="key-1", algorithm="HS256",
                public_key=b"public",
            )



if __name__ == "__main__":
    unittest.main()
