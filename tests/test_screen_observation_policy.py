import unittest

from providers.screen_observation_policy import (
    ScreenObservationGrant,
    ScreenObservationPolicyError,
    authorize_screen_observation,
    screen_observation_digest,
)


class ScreenObservationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.grant = ScreenObservationGrant(
            grant_id="grant-1", owner_id="owner-1", device_id="device-1",
            session_id="session-1", issued_at_epoch=100,
            expires_at_epoch=400, allowed_transports=("local", "ssh"),
        )
        self.request = dict(
            owner_id="owner-1", device_id="device-1", session_id="session-1",
            transport="ssh", now_epoch=200, image_size_bytes=1024,
        )

    def test_explicit_grant_authorizes_ssh_screen_observation(self):
        result = authorize_screen_observation(self.grant, **self.request)
        self.assertTrue(result["authorized"])
        self.assertTrue(result["redaction_required"])
        self.assertFalse(result["remote_input_allowed"])
        self.assertFalse(result["file_transfer_allowed"])

    def test_ssh_access_without_screen_grant_is_not_implicit_consent(self):
        with self.assertRaises(ScreenObservationPolicyError):
            authorize_screen_observation(
                ScreenObservationGrant(
                    grant_id="g", owner_id="owner-1", device_id="device-1",
                    session_id="session-1", issued_at_epoch=100,
                    expires_at_epoch=400, allowed_transports=("local",),
                ), **self.request
            )

    def test_wrong_owner_device_or_session_rejected(self):
        for field in ("owner_id", "device_id", "session_id"):
            request = dict(self.request)
            request[field] = "different"
            with self.subTest(field=field), self.assertRaises(ScreenObservationPolicyError):
                authorize_screen_observation(self.grant, **request)

    def test_expired_grant_rejected(self):
        request = dict(self.request, now_epoch=400)
        with self.assertRaises(ScreenObservationPolicyError):
            authorize_screen_observation(self.grant, **request)

    def test_capture_size_is_bounded(self):
        request = dict(self.request, image_size_bytes=9 * 1024 * 1024)
        with self.assertRaises(ScreenObservationPolicyError):
            authorize_screen_observation(self.grant, **request)

    def test_remote_actions_and_sensitive_capture_default_off(self):
        result = authorize_screen_observation(self.grant, **self.request)
        self.assertFalse(result["remote_input_allowed"])
        self.assertFalse(result["file_transfer_allowed"])
        self.assertTrue(result["redaction_required"])

    def test_screen_digest_is_stable_and_not_pixel_logging(self):
        self.assertEqual(screen_observation_digest(b"pixels"), screen_observation_digest(b"pixels"))
        self.assertEqual(len(screen_observation_digest(b"pixels")), 64)


if __name__ == "__main__":
    unittest.main()
