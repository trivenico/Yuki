import unittest

from automation.reviewer_signing import HMACReviewerSigner
from automation.host_reviewer_signing import (
    HostReviewerSigningKeyRegistry, ReviewerKeyRegistryError,
)


class HostReviewerSigningKeyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.allowed = True
        self.registry = HostReviewerSigningKeyRegistry(
            authorize=lambda reviewer, action: self.allowed and action in {
                "review:sign", "review:verify_signature"
            }
        )
        self.old = HMACReviewerSigner(key_id="key-v1", key=b"a" * 32)
        self.new = HMACReviewerSigner(key_id="key-v2", key=b"b" * 32)
        self.registry.register(reviewer_agent_id="reviewer-1", signer=self.old,
                              active_for_signing=True)

    def test_active_key_is_resolved_for_signing(self):
        self.assertIs(self.registry.resolve("reviewer-1", None), self.old)

    def test_rotation_keeps_old_key_for_verification(self):
        self.registry.register(reviewer_agent_id="reviewer-1", signer=self.new,
                              active_for_signing=True)
        self.assertIs(self.registry.resolve("reviewer-1", None), self.new)
        self.assertIs(self.registry.resolve("reviewer-1", "key-v1"), self.old)

    def test_revoked_key_cannot_sign_or_verify(self):
        self.registry.revoke(reviewer_agent_id="reviewer-1", key_id="key-v1")
        self.assertIsNone(self.registry.resolve("reviewer-1", None))
        self.assertIsNone(self.registry.resolve("reviewer-1", "key-v1"))

    def test_unknown_reviewer_and_denied_authorization_fail_closed(self):
        self.assertIsNone(self.registry.resolve("unknown", None))
        self.allowed = False
        self.assertIsNone(self.registry.resolve("reviewer-1", None))

    def test_duplicate_key_registration_is_rejected(self):
        with self.assertRaisesRegex(ReviewerKeyRegistryError, "already_registered"):
            self.registry.register(reviewer_agent_id="reviewer-1", signer=self.old)


if __name__ == "__main__":
    unittest.main()
