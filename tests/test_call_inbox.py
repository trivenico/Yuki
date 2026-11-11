import unittest
from unittest.mock import Mock

from automation.call_inbox import PairedDeviceCallInbox
from automation.remote_call_client import RemoteCallNotification


class PairedDeviceCallInboxTests(unittest.TestCase):
    def setUp(self):
        self.notification = RemoteCallNotification(
            notification_id="b" * 64,
            session_id="a" * 64,
            caller_device_id="dev_caller",
            recipient_device_id="dev_recipient",
            event_type="incoming_call",
            created_at="2026-11-11T12:00:00+00:00",
            expires_at="2026-11-11T12:01:30+00:00",
        )
        self.client = Mock()
        self.client.list_notifications.return_value = [self.notification]
        self.client.acknowledge_notification.return_value = True
        self.output = []

    def inbox(self, choice):
        return PairedDeviceCallInbox(
            self.client, prompt=lambda _: choice, output=self.output.append
        )

    def test_accept_requires_explicit_choice_then_acknowledges(self):
        result = self.inbox("a").run_once()
        self.client.accept.assert_called_once_with(self.notification.session_id)
        self.client.acknowledge_notification.assert_called_once_with(self.notification.notification_id)
        self.client.reject.assert_not_called()
        self.assertEqual(result, {"received": 1, "accepted": 1, "declined": 0, "ignored": 0})
        self.assertTrue(any("Media is not connected." in line for line in self.output))

    def test_decline_then_acknowledges(self):
        result = self.inbox("d").run_once()
        self.client.reject.assert_called_once_with(self.notification.session_id)
        self.client.acknowledge_notification.assert_called_once_with(self.notification.notification_id)
        self.client.accept.assert_not_called()
        self.assertEqual(result["declined"], 1)

    def test_unknown_choice_leaves_alert_unacknowledged(self):
        result = self.inbox("later").run_once()
        self.client.accept.assert_not_called()
        self.client.reject.assert_not_called()
        self.client.acknowledge_notification.assert_not_called()
        self.assertEqual(result["ignored"], 1)

    def test_ambiguous_accept_is_reconciled_without_replaying_action(self):
        self.client.accept.side_effect = RuntimeError("response lost")
        self.client.get_call.return_value = {"status": "accepted"}
        result = self.inbox("a").run_once()
        self.client.accept.assert_called_once_with(self.notification.session_id)
        self.client.get_call.assert_called_once_with(self.notification.session_id)
        self.client.acknowledge_notification.assert_called_once_with(
            self.notification.notification_id
        )
        self.assertEqual(result["accepted"], 1)

    def test_ambiguous_action_still_ringing_is_not_blindly_retried(self):
        self.client.accept.side_effect = RuntimeError("response lost")
        self.client.get_call.return_value = {"status": "ringing"}
        with self.assertRaisesRegex(RuntimeError, "response lost"):
            self.inbox("a").run_once()
        self.client.accept.assert_called_once_with(self.notification.session_id)
        self.client.get_call.assert_called_once_with(self.notification.session_id)
        self.client.acknowledge_notification.assert_not_called()

    def test_ack_failure_is_not_reported_as_success(self):
        self.client.acknowledge_notification.return_value = False
        with self.assertRaisesRegex(RuntimeError, "acknowledgement_failed"):
            self.inbox("a").run_once()

    def test_foreground_watch_does_not_reprompt_same_ignored_alert(self):
        prompts = []
        sleeps = []

        def prompt(message):
            prompts.append(message)
            return "i"

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise KeyboardInterrupt()

        inbox = PairedDeviceCallInbox(self.client, prompt=prompt, output=self.output.append)
        with self.assertRaises(KeyboardInterrupt):
            inbox.run_forever(poll_interval_seconds=2, sleep=sleep)

        self.assertEqual(self.client.list_notifications.call_count, 2)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(sleeps, [2, 2])

    def test_foreground_watch_rejects_unsafe_poll_interval(self):
        inbox = self.inbox("i")
        for interval in (0, 31, True, 1.5):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    inbox.run_forever(poll_interval_seconds=interval, sleep=lambda _: None)

    def test_empty_inbox_is_safe(self):
        self.client.list_notifications.return_value = []
        result = self.inbox("a").run_once()
        self.assertEqual(result["received"], 0)
        self.client.accept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
