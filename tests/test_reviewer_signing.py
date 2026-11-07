import unittest

from automation.reviewer_signing import (
    HMACReviewerSigner, ReviewerSignature, ReviewerSignatureError,
)


class HMACReviewerSignerTests(unittest.TestCase):
    def setUp(self):
        self.signer = HMACReviewerSigner(key_id="review-key-v1", key=b"x" * 32)
        self.payload = {
            "task_id": "task-1", "artifact_sha256": "a" * 64,
            "status": "passed", "findings": [],
        }

    def test_signature_verifies_for_exact_payload(self):
        signature = self.signer.sign(self.payload)
        self.assertTrue(self.signer.verify(self.payload, signature))

    def test_modified_payload_is_rejected(self):
        signature = self.signer.sign(self.payload)
        modified = dict(self.payload, status="failed")
        self.assertFalse(self.signer.verify(modified, signature))

    def test_wrong_key_id_is_rejected(self):
        signature = self.signer.sign(self.payload)
        other = ReviewerSignature("other-key", signature.algorithm, signature.signature_hex)
        self.assertFalse(self.signer.verify(self.payload, other))

    def test_short_key_is_rejected(self):
        with self.assertRaisesRegex(ReviewerSignatureError, "at_least_32_bytes"):
            HMACReviewerSigner(key_id="key", key=b"short")


if __name__ == "__main__":
    unittest.main()
