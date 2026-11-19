import unittest

from core.observation_policy import ObservationPolicy, ObservationSession
from core.observation_sources import (
    FakeObservationSource,
    ObservationEvent,
    ObservationGateway,
)


class ObservationSourceTests(unittest.TestCase):
    def setUp(self):
        self.session = ObservationSession(
            ObservationPolicy(enabled=True, allowed_sources=("app:ide",)),
            consent_granted=True,
        )
        self.source = FakeObservationSource("app:ide", capacity=2)
        self.gateway = ObservationGateway(self.session)

    def tearDown(self):
        self.source.close()

    def test_gateway_forwards_only_authorized_metadata(self):
        received = []
        self.assertTrue(self.source.publish(ObservationEvent(
            "app:ide", "development", {"application": "editor", "workspace": "yuki"},
        )))
        self.assertTrue(self.gateway.poll(self.source, received.append))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].metadata["workspace"], "yuki")

    def test_denied_or_sensitive_events_are_not_forwarded(self):
        received = []
        self.source.publish(ObservationEvent("app:ide", "credentials", {"application": "editor"}))
        self.assertFalse(self.gateway.poll(self.source, received.append))
        self.assertEqual(received, [])

    def test_pause_and_revoke_stop_event_delivery(self):
        received = []
        self.source.publish(ObservationEvent("app:ide", "development", {"application": "editor"}))
        self.session.pause()
        self.assertFalse(self.gateway.poll(self.source, received.append))
        self.session.revoke()
        self.source.publish(ObservationEvent("app:ide", "development", {"application": "editor"}))
        self.assertFalse(self.gateway.poll(self.source, received.append))
        self.assertEqual(received, [])

    def test_source_capacity_is_bounded_and_closed_source_rejects_events(self):
        event = ObservationEvent("app:ide", "development", {"application": "editor"})
        self.assertTrue(self.source.publish(event))
        self.assertTrue(self.source.publish(event))
        self.assertFalse(self.source.publish(event))
        self.source.close()
        self.assertFalse(self.source.publish(event))

    def test_event_metadata_is_bounded_and_scalar_only(self):
        with self.assertRaises(ValueError):
            ObservationEvent("app:ide", "development", {"screen": object()})
        with self.assertRaises(ValueError):
            ObservationEvent("app:ide", "development", {"title": "x" * 513})
        with self.assertRaises(ValueError):
            ObservationEvent("app:ide", "development", {"bad": float("nan")})

    def test_gateway_rejects_source_identity_mismatch(self):
        other = FakeObservationSource("browser:chrome")
        try:
            with self.assertRaises(ValueError):
                other.publish(ObservationEvent("app:ide", "development", {}))
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
