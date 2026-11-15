import json
import unittest

from automation.remote_inference_client import RemoteInferenceJobClient
from automation.local_inference_worker import LocalInferenceWorker
from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, amount=-1):
        return self.body[:amount]


class FakeTransport:
    def __init__(self, body, status=200):
        self.response = FakeResponse(body, status)
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class RemoteClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport(json.dumps({"job": {
            "job_id": "a" * 32, "model_id": "local/model", "input_text": "hello",
            "lease_expires_at": "2030-01-01T00:00:00+00:00", "lease_token": "t" * 43,
        }}).encode())
        self.client = RemoteInferenceJobClient(
            "https://yuki.example", "x" * 32, transport=self.transport,
        )

    def test_claim_parses_job_and_sends_device_auth(self):
        job = self.client.claim_next()
        self.assertEqual(job.input_text, "hello")
        self.assertEqual(job.lease_token, "t" * 43)
        self.assertEqual(self.transport.request.get_header("Authorization"), "Bearer " + "x" * 32)
        self.assertTrue(self.transport.request.full_url.endswith("/v1/inference/claim"))
        self.assertTrue(self.transport.response.closed)

    def test_idle_claim(self):
        self.transport.response = FakeResponse(b'{"job":null}')
        self.assertIsNone(self.client.claim_next())

    def test_complete_sends_only_result(self):
        self.transport.response = FakeResponse(b'{"job":{"status":"succeeded"}}')
        self.client.complete_job("x" * 32, "a" * 32, lease_token="t" * 43, result_text="ok")
        self.assertEqual(
            json.loads(self.transport.request.data),
            {"lease_token": "t" * 43, "result_text": "ok"},
        )

    def test_default_https_uses_pinned_firewall_transport(self):
        client = RemoteInferenceJobClient("https://yuki.example", "x" * 32)
        self.assertIsInstance(client._transport, PinnedHTTPSProviderTransport)

    def test_reject_insecure_remote_endpoint(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            RemoteInferenceJobClient("http://remote.example", "x" * 32)

    def test_rejects_response_overflow(self):
        self.client.max_response_bytes = 1024
        self.transport.response = FakeResponse(b"x" * 1025)
        with self.assertRaisesRegex(RuntimeError, "response_too_large"):
            self.client.claim_next()

    def test_maps_authentication_status_without_reading_body(self):
        self.transport.response = FakeResponse(b"secret response body", status=403)
        with self.assertRaisesRegex(PermissionError, "device_authentication_failed"):
            self.client.claim_next()
        self.assertTrue(self.transport.response.closed)

    def test_worker_accepts_transport_protocol(self):
        class Transport:
            lease_seconds = 120
            def claim_next(self, token):
                return None
            def complete_job(self, *args, **kwargs):
                pass

        class Provider:
            timeout = 45

        with self.assertRaisesRegex(TypeError, "OllamaProvider"):
            LocalInferenceWorker(Transport(), "x" * 32, {"model": Provider()})


if __name__ == "__main__":
    unittest.main()
