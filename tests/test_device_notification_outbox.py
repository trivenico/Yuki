import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.device_notification_outbox import DeviceNotificationOutbox
from core.device_pairing import DevicePairingService


class DeviceNotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pairing = DevicePairingService(root / "pairing.sqlite")
        self.outbox = DeviceNotificationOutbox(root / "outbox.sqlite", self.pairing)
        self.device_a = self._pair("owner-a", "Desktop A")
        self.device_b = self._pair("owner-a", "Desktop B")
        self.other_owner = self._pair("owner-b", "Desktop C")

    def _pair(self, owner, name):
        challenge = self.pairing.create_pairing_challenge(owner)
        return self.pairing.pair_device(challenge.code, name)

    def test_poll_is_scoped_to_authenticated_recipient_device(self):
        event = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.reminder_due", payload={"title": "Take a break"},
        )
        page_a = self.outbox.poll(self.device_a.token)
        page_b = self.outbox.poll(self.device_b.token)
        page_other = self.outbox.poll(self.other_owner.token)
        self.assertEqual([item.event_id for item in page_a.events], [event.event_id])
        self.assertEqual(page_b.events, [])
        self.assertEqual(page_other.events, [])

    def test_enqueue_requires_active_owner_bound_device(self):
        with self.assertRaises(PermissionError):
            self.outbox.enqueue(
                owner_id="owner-b", device_id=self.device_a.device.device_id,
                event_type="companion.notice", payload={},
            )
        self.assertTrue(self.pairing.revoke_device("owner-a", self.device_a.device.device_id))
        with self.assertRaises(PermissionError):
            self.outbox.enqueue(
                owner_id="owner-a", device_id=self.device_a.device.device_id,
                event_type="companion.notice", payload={},
            )

    def test_acknowledgement_is_recipient_scoped_and_idempotent(self):
        event = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.notice", payload={"body": "Ready"},
        )
        self.assertFalse(self.outbox.acknowledge(self.device_b.token, event.event_id))
        self.assertTrue(self.outbox.acknowledge(self.device_a.token, event.event_id))
        self.assertTrue(self.outbox.acknowledge(self.device_a.token, event.event_id))
        self.assertEqual(self.outbox.poll(self.device_a.token).events, [])

    def test_revoked_device_cannot_poll_or_acknowledge(self):
        event = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.notice", payload={},
        )
        self.pairing.revoke_device("owner-a", self.device_a.device.device_id)
        with self.assertRaises(PermissionError):
            self.outbox.poll(self.device_a.token)
        with self.assertRaises(PermissionError):
            self.outbox.acknowledge(self.device_a.token, event.event_id)

    def test_cursor_pagination_is_monotonic_and_bounded(self):
        device_id = self.device_a.device.device_id
        events = [
            self.outbox.enqueue(
                owner_id="owner-a", device_id=device_id,
                event_type="companion.notice", payload={"index": index},
            )
            for index in range(3)
        ]
        first = self.outbox.poll(self.device_a.token, limit=2)
        self.assertEqual([event.event_id for event in first.events],
                         [events[0].event_id, events[1].event_id])
        self.assertTrue(first.has_more)
        second = self.outbox.poll(
            self.device_a.token, after_sequence=first.next_cursor, limit=2
        )
        self.assertEqual([event.event_id for event in second.events], [events[2].event_id])
        self.assertFalse(second.has_more)
        with self.assertRaisesRegex(ValueError, "invalid_page_size"):
            self.outbox.poll(self.device_a.token, limit=101)

    def test_expired_events_are_not_delivered_or_acknowledged(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.notice", payload={}, now=now, ttl_seconds=60,
        )
        after_expiry = now + timedelta(seconds=61)
        self.assertEqual(self.outbox.poll(self.device_a.token, now=after_expiry).events, [])
        self.assertFalse(self.outbox.acknowledge(
            self.device_a.token, event.event_id, now=after_expiry
        ))

    def test_dedupe_key_returns_original_event(self):
        kwargs = dict(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.reminder_due", payload={"reminder_id": "r-1"},
            dedupe_key="reminder:r-1:due",
        )
        first = self.outbox.enqueue(**kwargs)
        second = self.outbox.enqueue(**kwargs)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.sequence, second.sequence)

    def test_duplicate_dedupe_key_is_isolated_per_device(self):
        first = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.notice", payload={}, dedupe_key="same-key",
        )
        second = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_b.device.device_id,
            event_type="companion.notice", payload={}, dedupe_key="same-key",
        )
        self.assertNotEqual(first.event_id, second.event_id)

    def test_payload_and_metadata_are_strictly_bounded(self):
        with self.assertRaisesRegex(ValueError, "notification_payload_too_large"):
            self.outbox.enqueue(
                owner_id="owner-a", device_id=self.device_a.device.device_id,
                event_type="companion.notice", payload={"body": "x" * 9000},
            )
        with self.assertRaisesRegex(ValueError, "invalid_event_type"):
            self.outbox.enqueue(
                owner_id="owner-a", device_id=self.device_a.device.device_id,
                event_type="RunShell", payload={},
            )
        with self.assertRaisesRegex(ValueError, "invalid_notification_payload"):
            self.outbox.enqueue(
                owner_id="owner-a", device_id=self.device_a.device.device_id,
                event_type="companion.notice", payload={"bad": float("nan")},
            )

    def test_outbox_survives_reopen(self):
        event = self.outbox.enqueue(
            owner_id="owner-a", device_id=self.device_a.device.device_id,
            event_type="companion.notice", payload={"title": "Persisted"},
        )
        reopened = DeviceNotificationOutbox(
            Path(self.temp.name) / "outbox.sqlite", self.pairing
        )
        self.assertEqual(reopened.poll(self.device_a.token).events[0], event)

    def test_invalid_cursor_and_token_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_cursor"):
            self.outbox.poll(self.device_a.token, after_sequence=True)
        with self.assertRaises(PermissionError):
            self.outbox.poll("not-a-valid-device-token")



class CompanionReminderDeviceChannelTests(unittest.TestCase):
    def test_reminder_channel_enqueues_once_per_active_owner_device(self):
        from companion.reminder_dispatcher import DeviceNotificationChannel

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        pairing = DevicePairingService(root / "pairing.sqlite")
        outbox = DeviceNotificationOutbox(root / "outbox.sqlite", pairing)
        first = self._pair(pairing, "owner-a", "Desktop A")
        second = self._pair(pairing, "owner-a", "Desktop B")
        self._pair(pairing, "owner-b", "Other owner")
        channel = DeviceNotificationChannel(pairing, outbox)

        count = channel({
            "owner_id": "owner-a",
            "reminder_id": "a" * 32,
            "text": "Meeting starts soon",
            "due_at": "2026-11-06T15:00:00+00:00",
            "idempotency_key": "a" * 32,
        })

        self.assertEqual(count, 2)
        self.assertEqual(len(outbox.poll(first.token).events), 1)
        self.assertEqual(len(outbox.poll(second.token).events), 1)
        self.assertEqual(len(outbox.poll(self._token_for_owner(pairing, "owner-b")).events), 0)

        # A dispatcher retry must return the same outbox events, not duplicate them.
        self.assertEqual(channel({
            "owner_id": "owner-a",
            "reminder_id": "a" * 32,
            "text": "Meeting starts soon",
            "due_at": "2026-11-06T15:00:00+00:00",
            "idempotency_key": "a" * 32,
        }), 2)
        self.assertEqual(len(outbox.poll(first.token).events), 1)

    def test_reminder_channel_rejects_invalid_envelopes(self):
        from companion.reminder_dispatcher import DeviceNotificationChannel

        class EmptyPairing:
            def list_devices(self, owner_id):
                return []

        class EmptyOutbox:
            def enqueue(self, **kwargs):
                raise AssertionError("must not enqueue invalid envelope")

        channel = DeviceNotificationChannel(EmptyPairing(), EmptyOutbox())
        with self.assertRaisesRegex(ValueError, "invalid_reminder_id"):
            channel({
                "owner_id": "owner-a", "reminder_id": "not-an-id",
                "text": "Reminder", "due_at": "2026-11-06T15:00:00+00:00",
            })
        with self.assertRaisesRegex(Exception, "no_active_paired_devices"):
            channel({
                "owner_id": "owner-a", "reminder_id": "b" * 32,
                "text": "Reminder", "due_at": "2026-11-06T15:00:00+00:00",
            })

    @staticmethod
    def _pair(pairing, owner, name):
        challenge = pairing.create_pairing_challenge(owner)
        return pairing.pair_device(challenge.code, name)

    @staticmethod
    def _token_for_owner(pairing, owner):
        challenge = pairing.create_pairing_challenge(owner)
        return pairing.pair_device(challenge.code, "Other desktop").token


if __name__ == "__main__":
    unittest.main()
