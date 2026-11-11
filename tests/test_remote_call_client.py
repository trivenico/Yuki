import json
import unittest
from unittest.mock import patch

from automation.remote_call_client import RemoteCallClient


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit=-1):
        return self.body[:limit]


class FakeOpener:
    def __init__(self, body):
        self.body = body
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return FakeResponse(self.body)


class RemoteCallClientTests(unittest.TestCase):
    def setUp(self):
        self.client = RemoteCallClient("https://yuki.example", "d" * 32)
        self.handle = "a" * 64
        self.invitation = {
            "session_id": self.handle,
            "caller_device_id": "dev_caller",
            "recipient_device_id": "dev_recipient",
            "status": "ringing",
            "created_at": "2026-11-11T12:00:00+00:00",
            "expires_at": "2026-11-11T12:01:30+00:00",
        }

    def test_inbox_uses_authenticated_get_and_validates_invitation(self):
        opener = FakeOpener(json.dumps({"invitations": [self.invitation]}).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            invitations = self.client.list_incoming()
        self.assertEqual(len(invitations), 1)
        self.assertEqual(invitations[0].caller_device_id, "dev_caller")
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + "d" * 32)
        self.assertTrue(opener.request.full_url.endswith("/v1/devices/calls/incoming"))

    def test_notification_inbox_validates_and_returns_opaque_alerts(self):
        notification = {
            "notification_id": "b" * 64,
            "session_id": self.handle,
            "caller_device_id": "dev_caller",
            "recipient_device_id": "dev_recipient",
            "event_type": "incoming_call",
            "created_at": "2026-11-11T12:00:00+00:00",
            "expires_at": "2026-11-11T12:01:30+00:00",
        }
        body = {"delivery": "at_least_once_polling", "notifications": [notification]}
        opener = FakeOpener(json.dumps(body).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            alerts = self.client.list_notifications()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].session_id, self.handle)
        self.assertTrue(opener.request.full_url.endswith("/v1/devices/calls/notifications/inbox"))
        self.assertEqual(opener.request.get_method(), "GET")

    def test_notification_acknowledgement_is_authenticated_post(self):
        notification_id = "b" * 64
        opener = FakeOpener(b'{"acknowledged":true}')
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertTrue(self.client.acknowledge_notification(notification_id))
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertTrue(opener.request.full_url.endswith(
            f"/v1/devices/calls/notifications/{notification_id}/ack"
        ))

    def test_rejects_malformed_notification(self):
        bad = {"delivery": "at_least_once_polling", "notifications": [{
            "notification_id": "raw-secret",
        }]}
        with patch("urllib.request.build_opener", return_value=FakeOpener(json.dumps(bad).encode())):
            with self.assertRaises(RuntimeError):
                self.client.list_notifications()

    def test_accept_and_reject_are_explicit_post_actions(self):
        for action, status in (("accept", "accepted"), ("reject", "rejected")):
            opener = FakeOpener(json.dumps({"session": {
                "session_id": self.handle, "status": status,
            }}).encode())
            with patch("urllib.request.build_opener", return_value=opener):
                result = getattr(self.client, action)(self.handle)
            self.assertEqual(result["status"], status)
            self.assertEqual(opener.request.get_method(), "POST")
            self.assertEqual(opener.request.data, b"{}")
            self.assertTrue(opener.request.full_url.endswith(
                f"/v1/devices/calls/{self.handle}/{action}"
            ))

    def test_rejects_invalid_inbox_and_duplicate_handles(self):
        invalid = dict(self.invitation, status="accepted")
        with patch("urllib.request.build_opener", return_value=FakeOpener(
            json.dumps({"invitations": [invalid]}).encode()
        )):
            with self.assertRaises(RuntimeError):
                self.client.list_incoming()
        with patch("urllib.request.build_opener", return_value=FakeOpener(
            json.dumps({"invitations": [self.invitation, self.invitation]}).encode()
        )):
            with self.assertRaises(RuntimeError):
                self.client.list_incoming()

    def test_rejects_insecure_remote_url_and_invalid_handles(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            RemoteCallClient("http://remote.example", "d" * 32)
        with self.assertRaises(ValueError):
            self.client.accept("not-a-session-handle")

    def test_rejects_oversized_response(self):
        client = RemoteCallClient(
            "https://yuki.example", "d" * 32, max_response_bytes=1024
        )
        with patch("urllib.request.build_opener", return_value=FakeOpener(b"x" * 1025)):
            with self.assertRaises(RuntimeError):
                client.list_incoming()


if __name__ == "__main__":
    unittest.main()
