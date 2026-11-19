import unittest

from core.observation_policy import ObservationPolicy
from core.observation_session_manager import ObservationSessionManager
from core.observation_sources import FakeObservationSource, ObservationEvent


class ObservationSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ObservationSessionManager(max_sessions=2, max_sources_per_session=2)
        self.policy = ObservationPolicy(enabled=True, allowed_sources=("app:ide", "browser:docs"))
        self.session_id = self.manager.create_session(self.policy, consent_granted=True)
        self.source = FakeObservationSource("app:ide")
        self.manager.register_source(self.session_id, self.source)

    def tearDown(self):
        try:
            self.manager.revoke(self.session_id)
        except KeyError:
            pass

    def test_status_exposes_indicator_and_session_lifecycle(self):
        self.assertTrue(self.manager.status(self.session_id).indicator_active)
        self.manager.pause(self.session_id)
        self.assertEqual(self.manager.status(self.session_id).state, "paused")
        self.assertFalse(self.manager.status(self.session_id).indicator_active)
        self.assertTrue(self.manager.resume(self.session_id))
        self.assertTrue(self.manager.status(self.session_id).indicator_active)

    def test_revoke_closes_source_and_clears_queued_events(self):
        event = ObservationEvent("app:ide", "development", {"app": "editor"})
        self.source.publish(event)
        self.manager.revoke(self.session_id)
        self.assertEqual(self.manager.status(self.session_id).state, "revoked")
        self.assertEqual(self.manager.status(self.session_id).registered_sources, 0)
        self.assertFalse(self.manager.poll(self.session_id, "app:ide", lambda item: None))
        self.assertFalse(self.source.publish(event))

    def test_event_limit_stops_session_and_closes_sources(self):
        self.manager.revoke(self.session_id)
        sid = self.manager.create_session(self.policy, consent_granted=True, event_limit=1)
        src = FakeObservationSource("app:ide")
        self.manager.register_source(sid, src)
        src.publish(ObservationEvent("app:ide", "development", {"app": "editor"}))
        received = []
        self.assertTrue(self.manager.poll(sid, "app:ide", received.append))
        self.assertEqual(self.manager.status(sid).state, "limit_reached")
        self.assertFalse(self.manager.status(sid).indicator_active)
        self.assertFalse(src.publish(ObservationEvent("app:ide", "development", {})))
        self.manager.revoke(sid)

    def test_source_must_be_allowlisted_and_unique(self):
        with self.assertRaises(PermissionError):
            self.manager.register_source(self.session_id, FakeObservationSource("app:unknown"))
        with self.assertRaises(ValueError):
            self.manager.register_source(self.session_id, self.source)

    def test_session_and_source_capacity_are_bounded(self):
        with self.assertRaises(RuntimeError):
            self.manager.create_session(self.policy, consent_granted=True)
        self.manager.register_source(self.session_id, FakeObservationSource("browser:docs"))
        with self.assertRaises(RuntimeError):
            self.manager.register_source(self.session_id, FakeObservationSource("browser:extra"))

    def test_nonconsented_session_never_activates(self):
        sid = self.manager.create_session(self.policy, consent_granted=False)
        self.assertEqual(self.manager.status(sid).state, "disabled")
        self.assertFalse(self.manager.status(sid).indicator_active)
        self.assertFalse(self.manager.resume(sid))


if __name__ == "__main__":
    unittest.main()
