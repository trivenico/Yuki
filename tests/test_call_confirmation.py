import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.call_confirmation import (
    CallConfirmationDenied,
    SQLiteCallConfirmationStore,
)


class SQLiteCallConfirmationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "calls.sqlite"
        self.store = SQLiteCallConfirmationStore(self.db, ttl_seconds=30)
        self.now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
        self.clock = patch("core.call_confirmation._now", side_effect=lambda: self.now)
        self.clock.start()
        self.owner = "owner-1"
        self.session = "authenticated-session-token-0001"
        self.caller = "device-caller"
        self.recipient = "device-recipient"

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def issue(self):
        return self.store.issue(
            owner_id=self.owner,
            authenticated_session_token=self.session,
            caller_device_id=self.caller,
            recipient_device_id=self.recipient,
        )

    def consume(self, token, **overrides):
        values = {
            "token": token,
            "owner_id": self.owner,
            "authenticated_session_token": self.session,
            "caller_device_id": self.caller,
            "recipient_device_id": self.recipient,
        }
        values.update(overrides)
        return self.store.consume(**values)

    def test_token_is_opaque_bound_to_session_and_exact_call_intent(self):
        token, expires = self.issue()
        self.assertTrue(token)
        self.assertEqual(expires, (self.now + timedelta(seconds=30)).isoformat())
        with sqlite3.connect(self.db) as db:
            stored = db.execute(
                "SELECT token_hash, session_hash FROM call_confirmation_challenges"
            ).fetchone()
        self.assertNotIn(token, stored)
        self.assertNotEqual(stored[0], token)
        self.assertNotEqual(stored[1], self.session)

        with self.assertRaises(CallConfirmationDenied):
            self.consume(token, recipient_device_id="another-device")
        with self.assertRaises(CallConfirmationDenied):
            self.consume(token, authenticated_session_token="different-session-token")
        with self.assertRaises(CallConfirmationDenied):
            self.consume(token, owner_id="another-owner")

        # Failed mismatched attempts do not consume a legitimate owner's challenge.
        self.consume(token)
        with self.assertRaises(CallConfirmationDenied):
            self.consume(token)

    def test_expired_challenge_is_rejected(self):
        token, _ = self.issue()
        self.now += timedelta(seconds=31)
        with self.assertRaises(CallConfirmationDenied):
            self.consume(token)

    def test_challenge_cannot_be_issued_for_same_device(self):
        with self.assertRaises(ValueError):
            self.store.issue(
                owner_id=self.owner,
                authenticated_session_token=self.session,
                caller_device_id=self.caller,
                recipient_device_id=self.caller,
            )

    def test_active_challenge_count_is_bounded(self):
        for _ in range(10):
            self.issue()
        with self.assertRaises(CallConfirmationDenied):
            self.issue()

    def test_challenge_is_single_use_under_concurrent_consumption(self):
        import threading

        token, _ = self.issue()
        outcomes = []
        lock = threading.Lock()

        def consume_once():
            try:
                self.consume(token)
                result = "consumed"
            except CallConfirmationDenied:
                result = "denied"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=consume_once) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("consumed"), 1)
        self.assertEqual(outcomes.count("denied"), 7)


if __name__ == "__main__":
    unittest.main()
