import unittest

from core.google_oauth_identity import (
    GOOGLE_GMAIL_MINIMUM_SCOPES,
    GoogleAccountIdentityFactory,
    GoogleOAuthIdentityError,
)


class GoogleAccountIdentityFactoryTests(unittest.TestCase):
    def setUp(self):
        self.factory = GoogleAccountIdentityFactory()

    def test_maps_verified_subject_to_stable_opaque_account_id(self):
        identity = self.factory.create({
            "issuer": "https://accounts.google.com",
            "subject": "109876543210987654321",
        })
        self.assertEqual(identity.provider_id, "google")
        self.assertTrue(identity.account_id.startswith("google-"))
        self.assertEqual(len(identity.account_id), 71)
        self.assertEqual(
            identity.account_id,
            self.factory.create({
                "issuer": "https://accounts.google.com",
                "subject": "109876543210987654321",
            }).account_id,
        )
        self.assertNotIn("109876543210987654321", identity.account_id)

    def test_accepts_google_legacy_issuer_and_normalizes(self):
        identity = self.factory.create({
            "issuer": "accounts.google.com", "subject": "subject_123",
        })
        self.assertEqual(identity.issuer, "https://accounts.google.com")

    def test_subject_change_maps_to_different_account(self):
        first = self.factory.create({
            "issuer": "https://accounts.google.com", "subject": "subject-a",
        })
        second = self.factory.create({
            "issuer": "https://accounts.google.com", "subject": "subject-b",
        })
        self.assertNotEqual(first.account_id, second.account_id)

    def test_rejects_untrusted_issuer(self):
        with self.assertRaisesRegex(GoogleOAuthIdentityError, "issuer_invalid"):
            self.factory.create({"issuer": "https://attacker.example", "subject": "x"})

    def test_rejects_invalid_or_missing_subject(self):
        for subject in (None, "", "contains spaces", "x" * 256, "email@example.com"):
            with self.subTest(subject=subject), self.assertRaises(GoogleOAuthIdentityError):
                self.factory.create({
                    "issuer": "https://accounts.google.com", "subject": subject,
                })

    def test_rejects_non_mapping_and_unverified_claims_shape(self):
        with self.assertRaises(GoogleOAuthIdentityError):
            self.factory.create(None)
        with self.assertRaises(GoogleOAuthIdentityError):
            self.factory.create({"sub": "subject-without-verified-issuer"})

    def test_declares_only_minimum_gmail_scopes_needed_for_send_and_reconcile(self):
        self.assertEqual(
            set(GOOGLE_GMAIL_MINIMUM_SCOPES),
            {
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.readonly",
            },
        )


if __name__ == "__main__":
    unittest.main()
