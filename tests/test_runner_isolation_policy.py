import unittest
from dataclasses import replace

from evaluation.runner_isolation_policy import create_runner_isolation_policy


class RunnerIsolationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = create_runner_isolation_policy(
            uid=65532, gid=65532, seccomp_profile_digest="a" * 64
        )

    def test_default_policy_is_valid_and_fingerprinted(self):
        self.assertTrue(self.policy.verify())
        self.assertEqual(self.policy.network_mode, "none")
        self.assertTrue(self.policy.read_only_rootfs)
        self.assertTrue(self.policy.no_new_privileges)
        self.assertTrue(self.policy.drop_all_capabilities)

    def test_root_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            create_runner_isolation_policy(
                uid=0, gid=65532, seccomp_profile_digest="a" * 64
            )

    def test_network_enablement_invalidates_policy(self):
        self.assertFalse(replace(self.policy, network_mode="bridge").verify())

    def test_relaxed_privilege_control_invalidates_policy(self):
        self.assertFalse(replace(self.policy, no_new_privileges=False).verify())

    def test_digest_tampering_is_detected(self):
        self.assertFalse(replace(self.policy, memory_limit_bytes=1024**3).verify())

    def test_bool_is_not_accepted_as_integer_limit(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            create_runner_isolation_policy(
                uid=65532, gid=65532, seccomp_profile_digest="a" * 64,
                pids_limit=True,
            )

    def test_invalid_seccomp_digest_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            create_runner_isolation_policy(
                uid=65532, gid=65532, seccomp_profile_digest="not-a-digest"
            )


if __name__ == "__main__":
    unittest.main()
