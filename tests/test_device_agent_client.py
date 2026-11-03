import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.device_agent_client import DeviceAgentClient, discover_capabilities


class _Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()
        self.closed = False
    def read(self, limit=-1):
        return self.value[:limit]
    def close(self):
        self.closed = True


class DeviceAgentClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sequence = Path(self.temp.name) / "sequence"
        self.opener = Mock()
        self.client = DeviceAgentClient(
            "https://yuki.example.test", "x" * 32,
            sequence_path=self.sequence, opener=self.opener,
        )

    def test_endpoint_requires_tls_except_loopback(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            DeviceAgentClient("http://remote.example", "x" * 32, sequence_path=self.sequence)
        local = DeviceAgentClient("http://127.0.0.1:8000", "x" * 32, sequence_path=self.sequence)
        self.assertTrue(local.endpoint.startswith("http://"))

    def test_heartbeat_posts_bearer_and_increments_persistent_sequence(self):
        self.opener.open.side_effect = [
            _Response({"sequence": 1}), _Response({"sequence": 2})
        ]
        self.assertEqual(self.client.send_heartbeat({"os": "Linux"})["sequence"], 1)
        self.assertEqual(self.client.send_heartbeat({"os": "Linux"})["sequence"], 2)
        request = self.opener.open.call_args_list[0].args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "x" * 32)
        self.assertEqual(self.sequence.read_text(), "2")

    def test_bad_acknowledgement_is_rejected(self):
        self.opener.open.return_value = _Response({"sequence": 99})
        with self.assertRaisesRegex(RuntimeError, "acknowledgement"):
            self.client.send_heartbeat({})

    def test_corrupt_sequence_fails_closed(self):
        self.sequence.write_text("not-a-number")
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            self.client.send_heartbeat({})

    def test_payload_and_status_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "invalid status"):
            self.client.send_heartbeat({}, status="execute")
        with self.assertRaisesRegex(ValueError, "too large"):
            self.client.send_heartbeat({"os": "x" * 16000})

    def test_discovery_reports_only_bounded_host_metadata(self):
        caps = discover_capabilities()
        self.assertIn("os", caps)
        self.assertIn("architecture", caps)
        self.assertIn("cpu_cores", caps)
        self.assertLessEqual(len(json.dumps(caps)), 12000)
        self.assertNotIn("hostname", caps)
        self.assertNotIn("username", caps)


if __name__ == "__main__":
    unittest.main()
