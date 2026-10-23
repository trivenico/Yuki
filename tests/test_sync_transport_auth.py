"""Tests for authenticated sync request signing and replay protection."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.sync_transport_auth import (
    NonceReplayStore,
    SignedRequest,
    TransportAuthError,
    sign_request,
    verify_request,
)

CREDENTIAL = "a" * 64
NOW = 1_798_000_000


class SyncTransportAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = NonceReplayStore(str(Path(self.temp.name) / "nonces.sqlite"))

    def tearDown(self):
        self.temp.cleanup()

    def request(self, **overrides):
        args = {
            "device_id": "laptop-01",
            "method": "POST",
            "path": "/sync/events",
            "timestamp": NOW,
            "nonce": "nonce_value_0123456789",
            "body": b'{"schema_version":1}',
            "credential": CREDENTIAL,
        }
        args.update(overrides)
        return sign_request(**args)

    def test_valid_signature_is_accepted_once(self):
        request = self.request()
        verify_request(request, credential=CREDENTIAL, replay_store=self.store, now=NOW)
        with self.assertRaises(TransportAuthError):
            verify_request(request, credential=CREDENTIAL, replay_store=self.store, now=NOW)

    def test_wrong_credential_rejected(self):
        request = self.request()
        with self.assertRaises(TransportAuthError):
            verify_request(request, credential="b" * 64, replay_store=self.store, now=NOW)

    def test_tampered_body_rejected(self):
        request = self.request()
        tampered = SignedRequest(request.device_id, request.method, request.path,
                                 request.timestamp, request.nonce, b"changed", request.signature)
        with self.assertRaises(TransportAuthError):
            verify_request(tampered, credential=CREDENTIAL, replay_store=self.store, now=NOW)

    def test_stale_timestamp_rejected(self):
        request = self.request(timestamp=NOW - 600, nonce="nonce_stale_0123456789")
        with self.assertRaises(TransportAuthError):
            verify_request(request, credential=CREDENTIAL, replay_store=self.store, now=NOW)

    def test_invalid_path_rejected(self):
        with self.assertRaises(TransportAuthError):
            self.request(path="/sync/../admin", nonce="nonce_path_0123456789")

    def test_oversized_body_rejected(self):
        with self.assertRaises(TransportAuthError):
            self.request(body=b"x" * (64 * 1024 + 1), nonce="nonce_big_0123456789")

    def test_wrong_signature_format_rejected(self):
        request = self.request()
        tampered = SignedRequest(request.device_id, request.method, request.path,
                                 request.timestamp, request.nonce, request.body, "not-a-signature")
        with self.assertRaises(TransportAuthError):
            verify_request(tampered, credential=CREDENTIAL, replay_store=self.store, now=NOW)

    def test_replay_is_blocked_across_store_reopen(self):
        request = self.request()
        verify_request(request, credential=CREDENTIAL, replay_store=self.store, now=NOW)
        reopened = NonceReplayStore(self.store.database_path)
        with self.assertRaises(TransportAuthError):
            verify_request(request, credential=CREDENTIAL, replay_store=reopened, now=NOW)


if __name__ == "__main__":
    unittest.main()
