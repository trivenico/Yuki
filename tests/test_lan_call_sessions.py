"""Security and lifecycle tests for LAN call invitations (no media transport)."""
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from pathlib import Path

from core.device_pairing import DevicePairingService
from core import lan_call_sessions
from core.lan_call_sessions import LANCallSessionStore, CallSessionDenied, CallSessionError


class LANCallSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pairing = DevicePairingService(str(Path(self.tmp.name) / "devices.sqlite3"))
        self.caller = self._pair("Caller")
        self.recipient = self._pair("Recipient")
        self.other = self._pair("Other")
        self.calls = LANCallSessionStore(self.pairing, invitation_ttl_seconds=30)

    def tearDown(self):
        self.tmp.cleanup()

    def _pair(self, name):
        challenge = self.pairing.create_pairing_challenge("owner")
        return self.pairing.pair_device(challenge.code, name)

    def _invite(self, **kwargs):
        values = {"caller_token": self.caller.token,
                  "recipient_device_id": self.recipient.device.device_id,
                  "owner_confirmed": True}
        values.update(kwargs)
        return self.calls.invite(**values)

    def test_invitation_requires_literal_owner_confirmation(self):
        for value in (False, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(CallSessionDenied):
                self._invite(owner_confirmed=value)

    def test_recipient_authenticated_acceptance_is_explicit_consent(self):
        invite = self._invite()
        with self.assertRaises(CallSessionDenied):
            self.calls.accept(session_id=invite.session_id, recipient_token=self.other.token)
        accepted = self.calls.accept(session_id=invite.session_id,
                                     recipient_token=self.recipient.token)
        self.assertEqual(accepted.status, "accepted")
        self.assertIsNotNone(accepted.accepted_at)
        with self.assertRaises(CallSessionDenied):
            self.calls.accept(session_id=invite.session_id,
                              recipient_token=self.recipient.token)

    def test_recipient_can_reject_and_caller_can_cancel(self):
        rejected = self._invite()
        self.assertEqual(self.calls.reject(session_id=rejected.session_id,
                                           recipient_token=self.recipient.token).status, "rejected")
        cancelled = self._invite()
        self.assertEqual(self.calls.end(session_id=cancelled.session_id,
                                        device_token=self.caller.token).status, "ended")

    def test_either_participant_can_end_accepted_call(self):
        invite = self._invite()
        self.calls.accept(session_id=invite.session_id, recipient_token=self.recipient.token)
        ended = self.calls.end(session_id=invite.session_id, device_token=self.recipient.token)
        self.assertEqual(ended.status, "ended")

    def test_revoked_device_loses_session_access(self):
        invite = self._invite()
        self.pairing.revoke_device("owner", self.recipient.device.device_id)
        with self.assertRaises(CallSessionDenied):
            self.calls.accept(session_id=invite.session_id, recipient_token=self.recipient.token)

    def test_foreign_owner_recipient_is_denied_but_same_owner_devices_can_call(self):
        challenge = self.pairing.create_pairing_challenge("another-owner")
        foreign = self.pairing.pair_device(challenge.code, "Foreign")
        with self.assertRaises(CallSessionDenied):
            self._invite(recipient_device_id=foreign.device.device_id)
        # A third active device belonging to the same owner is a valid caller.
        same_owner_invite = self.calls.invite(caller_token=self.other.token,
            recipient_device_id=self.recipient.device.device_id, owner_confirmed=True)
        self.assertEqual(same_owner_invite.status, "ringing")


    def test_owner_invitation_requires_two_active_devices_owned_by_owner(self):
        invite = self.calls.invite_as_owner(
            owner_id="owner",
            caller_device_id=self.caller.device.device_id,
            recipient_device_id=self.recipient.device.device_id,
        )
        self.assertEqual(invite.status, "ringing")
        with self.assertRaises(CallSessionDenied):
            self.calls.invite_as_owner(
                owner_id="owner",
                caller_device_id=self.caller.device.device_id,
                recipient_device_id=self.other.device.device_id + "-missing",
            )
        challenge = self.pairing.create_pairing_challenge("foreign-owner")
        foreign = self.pairing.pair_device(challenge.code, "Foreign")
        with self.assertRaises(CallSessionDenied):
            self.calls.invite_as_owner(
                owner_id="owner",
                caller_device_id=self.caller.device.device_id,
                recipient_device_id=foreign.device.device_id,
            )

    def test_same_device_call_and_malformed_session_are_rejected(self):
        with self.assertRaises(CallSessionError):
            self._invite(recipient_device_id=self.caller.device.device_id)
        with self.assertRaises(CallSessionError):
            self.calls.get(session_id="bad", device_token=self.caller.token)

    def test_session_id_is_not_stored_in_plaintext(self):
        invite = self._invite()
        with open(self.pairing.database_path, "rb") as handle:
            self.assertNotIn(invite.session_id.encode("ascii"), handle.read())

    def test_participant_only_status_reads(self):
        invite = self._invite()
        self.assertEqual(self.calls.get(session_id=invite.session_id,
                                        device_token=self.caller.token).status, "ringing")
        with self.assertRaises(CallSessionDenied):
            self.calls.get(session_id=invite.session_id, device_token=self.other.token)

    def test_expired_invitation_cannot_be_accepted_and_is_persisted_expired(self):
        invite = self._invite()
        future = lan_call_sessions._now() + timedelta(seconds=31)
        with patch("core.lan_call_sessions._now", return_value=future):
            expired = self.calls.accept(session_id=invite.session_id,
                                        recipient_token=self.recipient.token)
        self.assertEqual(expired.status, "expired")
        self.assertEqual(self.calls.get(session_id=invite.session_id,
                                        device_token=self.caller.token).status, "expired")

    def test_concurrent_acceptance_has_exactly_one_winner(self):
        invite = self._invite()
        def accept():
            try:
                return self.calls.accept(session_id=invite.session_id,
                                          recipient_token=self.recipient.token).status
            except CallSessionDenied:
                return "denied"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: accept(), range(2)))
        self.assertCountEqual(outcomes, ["accepted", "denied"])
        self.assertEqual(self.calls.get(session_id=invite.session_id,
                                        device_token=self.caller.token).status, "accepted")


    def test_notification_outbox_is_atomic_recipient_scoped_and_acknowledged(self):
        invite = self.calls.invite_as_owner(
            owner_id="owner",
            caller_device_id=self.caller.device.device_id,
            recipient_device_id=self.recipient.device.device_id,
        )
        notifications = self.calls.list_notifications(device_token=self.recipient.token)
        self.assertEqual(len(notifications), 1)
        event = notifications[0]
        self.assertEqual(event["event_type"], "incoming_call")
        self.assertEqual(
            event["session_id"], hashlib.sha256(invite.session_id.encode("ascii")).hexdigest()
        )
        self.assertEqual(len(event["notification_id"]), 64)
        self.assertNotIn(invite.session_id, repr(event))
        self.assertEqual(self.calls.list_notifications(device_token=self.other.token), [])
        self.assertTrue(self.calls.acknowledge_notification(
            notification_id=event["notification_id"], device_token=self.recipient.token
        ))
        self.assertEqual(self.calls.list_notifications(device_token=self.recipient.token), [])
        self.assertTrue(self.calls.acknowledge_notification(
            notification_id=event["notification_id"], device_token=self.recipient.token
        ))

    def test_notification_is_suppressed_after_invite_cancellation_or_caller_revocation(self):
        invite = self.calls.invite_as_owner(
            owner_id="owner",
            caller_device_id=self.caller.device.device_id,
            recipient_device_id=self.recipient.device.device_id,
        )
        self.calls.cancel_as_owner(owner_id="owner", session_id=invite.session_id)
        self.assertEqual(self.calls.list_notifications(device_token=self.recipient.token), [])
        second = self.calls.invite_as_owner(
            owner_id="owner",
            caller_device_id=self.caller.device.device_id,
            recipient_device_id=self.recipient.device.device_id,
        )
        self.pairing.revoke_device("owner", self.caller.device.device_id)
        self.assertEqual(self.calls.list_notifications(device_token=self.recipient.token), [])

if __name__ == "__main__":
    unittest.main()
