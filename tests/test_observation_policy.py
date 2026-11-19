import unittest

from core.observation_policy import ObservationPolicy, ObservationSession


class ObservationPolicyTests(unittest.TestCase):
    def test_disabled_by_default_and_empty_allowlist_fail_closed(self):
        policy = ObservationPolicy()
        session = ObservationSession(policy, consent_granted=True)
        self.assertEqual(session.state, "disabled")
        self.assertFalse(session.authorize("browser:chrome"))

    def test_requires_both_enabled_policy_and_explicit_consent(self):
        policy = ObservationPolicy(enabled=True, allowed_sources=("browser:chrome",))
        self.assertFalse(ObservationSession(policy, consent_granted=False).authorize("browser:chrome"))
        self.assertTrue(ObservationSession(policy, consent_granted=True).authorize("browser:chrome"))

    def test_denylist_and_sensitive_categories_override_allowlist(self):
        policy = ObservationPolicy(
            enabled=True,
            allowed_sources=("browser:chrome", "app:ide"),
            denied_sources=("browser:chrome",),
        )
        session = ObservationSession(policy, consent_granted=True)
        self.assertFalse(session.authorize("browser:chrome"))
        self.assertFalse(session.authorize("app:ide", "credentials"))
        self.assertTrue(session.authorize("app:ide", "development"))

    def test_pause_resume_and_revoke_lifecycle(self):
        session = ObservationSession(
            ObservationPolicy(enabled=True, allowed_sources=("app:ide",)),
            consent_granted=True,
        )
        session.pause()
        self.assertEqual(session.state, "paused")
        self.assertFalse(session.authorize("app:ide"))
        self.assertTrue(session.resume())
        self.assertTrue(session.authorize("app:ide"))
        session.revoke()
        self.assertEqual(session.state, "revoked")
        self.assertFalse(session.resume())
        self.assertFalse(session.authorize("app:ide"))

    def test_audit_sink_receives_metadata_only_and_failure_fails_closed(self):
        events = []
        session = ObservationSession(
            ObservationPolicy(enabled=True, allowed_sources=("app:ide",)),
            consent_granted=True,
            audit_sink=events.append,
        )
        self.assertTrue(session.authorize("app:ide", "development"))
        self.assertEqual(events[0], {
            "event": "observation.authorization",
            "source": "app:ide",
            "category": "development",
            "allowed": True,
            "reason": "explicitly_allowed",
        })
        failing = ObservationSession(
            ObservationPolicy(enabled=True, allowed_sources=("app:ide",)),
            consent_granted=True,
            audit_sink=lambda event: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
        )
        self.assertFalse(failing.authorize("app:ide"))

    def test_invalid_identifiers_and_policy_bounds_are_rejected(self):
        session = ObservationSession(
            ObservationPolicy(enabled=True, allowed_sources=("app:ide",)),
            consent_granted=True,
        )
        self.assertFalse(session.authorize("app:ide", "credentials\nsecret"))
        with self.assertRaises(ValueError):
            ObservationPolicy(enabled=True, allowed_sources=tuple(f"app:{i}" for i in range(101)))

    def test_policy_is_immutable_and_normalizes_identifiers(self):
        policy = ObservationPolicy(enabled=True, allowed_sources=(" App:IDE ",))
        self.assertEqual(policy.allowed_sources, ("app:ide",))
        with self.assertRaises(Exception):
            policy.enabled = False


if __name__ == "__main__":
    unittest.main()
