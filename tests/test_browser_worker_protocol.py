import json
import unittest

from automation.browser_worker_protocol import (
    BrowserTaskRequest,
    BrowserTaskResponse,
    BrowserWorkerProtocolError,
)


class BrowserWorkerProtocolTests(unittest.TestCase):
    def test_request_round_trip_and_defaults(self):
        request = BrowserTaskRequest("task_1", "policy-v1", "https://example.com/")
        decoded = BrowserTaskRequest.from_json(request.to_json())
        self.assertEqual(decoded, request)
        self.assertEqual(decoded.timeout_ms, 10_000)

    def test_request_rejects_unknown_or_missing_fields(self):
        request = BrowserTaskRequest("task_1", "policy-v1", "https://example.com/")
        payload = json.loads(request.to_json())
        payload["shell"] = "true"
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskRequest.from_json(json.dumps(payload))
        del payload["shell"]
        del payload["timeout_ms"]
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskRequest.from_json(json.dumps(payload))

    def test_request_rejects_duplicate_json_keys(self):
        raw = (
            '{"task_id":"task_1","policy_revision":"policy-v1",'
            '"url":"https://example.com/","timeout_ms":10000,'
            '"max_text_chars":8000,"timeout_ms":20000}'
        )
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskRequest.from_json(raw)

    def test_request_rejects_ip_literal_credentials_and_non_https(self):
        for url in (
            "https://127.0.0.1/",
            "https://user:pass@example.com/",
            "http://example.com/",
            "https://example.com:444/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(BrowserWorkerProtocolError):
                    BrowserTaskRequest("task_1", "policy-v1", url)

    def test_request_rejects_boolean_numeric_limits(self):
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskRequest("task_1", "policy-v1", "https://example.com/", timeout_ms=True)

    def test_response_round_trip(self):
        response = BrowserTaskResponse(
            "task_1", "ok", "https://example.com/", 200, "Example", "page text", 25
        )
        self.assertEqual(BrowserTaskResponse.from_json(response.to_json()), response)

    def test_non_success_response_cannot_carry_page_content(self):
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskResponse(
                "task_1", "error", "https://example.com/", None, "", "secret data", 3
            )

    def test_response_rejects_oversized_text(self):
        with self.assertRaises(BrowserWorkerProtocolError):
            BrowserTaskResponse(
                "task_1", "ok", "https://example.com/", 200, "", "x" * 12_001, 3
            )


if __name__ == "__main__":
    unittest.main()
