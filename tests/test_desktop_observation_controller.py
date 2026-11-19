import unittest

from core.desktop_observation_controller import DesktopObservationController
from core.observation_policy import ObservationPolicy
from core.observation_session_manager import ObservationSessionManager


class DesktopObservationControllerTests(unittest.TestCase):
    def setUp(self):
        self.indicator = []
        self.received = []
        self.policy = ObservationPolicy(
            enabled=True,
            allowed_sources=("desktop:metadata",),
        )
        self.controller = DesktopObservationController(
            self.policy,
            {"code.exe": "ide"},
            indicator=self.indicator.append,
            manager=ObservationSessionManager(),
            reader=lambda: "code.exe",
        )

    def test_start_requires_explicit_consent(self):
        with self.assertRaises(PermissionError):
            self.controller.start(consent_granted=False)
        self.assertEqual(self.indicator, [])
        self.assertEqual(self.controller.state, "stopped")

    def test_start_poll_pause_resume_and_revoke(self):
        session_id = self.controller.start(consent_granted=True)
        self.assertTrue(session_id)
        self.assertEqual(self.controller.state, "active")
        self.assertEqual(self.indicator, [True])

        self.assertTrue(self.controller.poll_once(self.received.append))
        self.assertEqual(dict(self.received[0].metadata), {"application_id": "ide"})

        self.controller.pause()
        self.assertEqual(self.controller.state, "paused")
        self.assertFalse(self.controller.poll_once(self.received.append))
        self.assertEqual(self.indicator, [True, False])

        self.assertTrue(self.controller.resume())
        self.assertEqual(self.indicator, [True, False, True])
        self.assertTrue(self.controller.poll_once(self.received.append))
        self.assertEqual(len(self.received), 2)

        self.controller.revoke()
        self.assertEqual(self.controller.state, "revoked")
        self.assertFalse(self.controller.poll_once(self.received.append))
        self.assertEqual(self.indicator[-1], False)

    def test_policy_must_allow_desktop_metadata(self):
        policy = ObservationPolicy(enabled=True, allowed_sources=())
        with self.assertRaises(PermissionError):
            DesktopObservationController(
                policy, {"code.exe": "ide"}, indicator=lambda active: None,
            )

    def test_indicator_failure_during_start_revokes_session(self):
        manager = ObservationSessionManager()

        def fail(_active):
            raise RuntimeError("tray unavailable")

        controller = DesktopObservationController(
            self.policy,
            {"code.exe": "ide"},
            indicator=fail,
            manager=manager,
            reader=lambda: "code.exe",
        )
        with self.assertRaises(RuntimeError):
            controller.start(consent_granted=True)
        self.assertEqual(controller.state, "stopped")
        # Failed startup must not leave a live, authorized session behind.
        self.assertFalse(controller.poll_once(lambda event: None))

    def test_event_limit_stops_collector_and_turns_indicator_off(self):
        controller = DesktopObservationController(
            self.policy,
            {"code.exe": "ide"},
            indicator=self.indicator.append,
            manager=ObservationSessionManager(),
            reader=lambda: "code.exe",
            event_limit=1,
        )
        controller.start(consent_granted=True)
        self.assertTrue(controller.poll_once(lambda event: None))
        self.assertEqual(controller.state, "limit_reached")
        self.assertEqual(self.indicator, [True, False])
        self.assertFalse(controller.poll_once(lambda event: None))

    def test_consumer_receives_only_policy_authorized_event(self):
        self.controller.start(consent_granted=True)
        self.assertTrue(self.controller.poll_once(self.received.append))
        self.assertEqual(len(self.received), 1)
        self.controller.revoke()


if __name__ == "__main__":
    unittest.main()
