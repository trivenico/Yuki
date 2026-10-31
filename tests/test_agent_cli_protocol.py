import json
import unittest

from core.agent_cli_protocol import (
    AgentCLIEvent, content_events, event_line, new_request_id, new_session_id,
)


class AgentCLIProtocolTests(unittest.TestCase):
    def test_event_is_single_line_json_with_correlation_ids(self):
        session, request = new_session_id(), new_request_id()
        encoded = event_line("content", session, request, text="hello\nworld")
        self.assertNotIn("\n", encoded)
        item = json.loads(encoded)
        self.assertEqual(item["session_id"], session)
        self.assertEqual(item["request_id"], request)
        self.assertEqual(item["data"]["text"], "hello\nworld")

    def test_content_is_chunked_without_losing_unicode(self):
        text = "A🙂漢字" * 10
        events = list(content_events(text, session_id="s", request_id="r", chunk_chars=7))
        reconstructed = "".join(json.loads(item)["data"]["text"] for item in events)
        self.assertEqual(reconstructed, text)
        self.assertTrue(all(len(item.encode("utf-8")) <= 32000 for item in events))

    def test_rejects_unknown_event_and_non_finite_json(self):
        with self.assertRaises(ValueError):
            AgentCLIEvent("tool_call", "s", "r", {}).encode()
        with self.assertRaises(ValueError):
            AgentCLIEvent("content", "s", "r", {"bad": float("nan")}).encode()

    def test_rejects_oversized_event_and_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            AgentCLIEvent("content", "s", "r", {"text": "x" * 33000}).encode()
        with self.assertRaises(ValueError):
            list(content_events("hello", session_id="s", request_id="r", chunk_chars=0))


if __name__ == "__main__":
    unittest.main()