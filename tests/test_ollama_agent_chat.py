import json
import unittest
from unittest.mock import patch

from providers.ollama import OllamaProvider
from core.pinned_https_provider_transport import PinnedHTTPTransportError


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.payload[:limit]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, payload, requests):
        self.payload = payload
        self.requests = requests

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payload)



class FakeNDJSONResponse:
    def __init__(self, lines):
        self.lines = [line if isinstance(line, bytes) else line.encode() for line in lines]
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def close(self): pass
    def readline(self, limit):
        if not self.lines: return b""
        line = self.lines.pop(0)
        return line[:limit + 1] if len(line) > limit else line

class FakeNDJSONOpener:
    def __init__(self, lines, requests):
        self.response = FakeNDJSONResponse(lines)
        self.requests = requests
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response

class OllamaAgentChatTests(unittest.TestCase):
    def setUp(self):
        self.provider = OllamaProvider(model="test-model")
        self.messages = [
            {"role": "system", "content": "You are Yuki"},
            {"role": "user", "content": "Find a value"},
        ]
        self.tools = [{
            "type": "function",
            "function": {
                "name": "lookup", "description": "Look up a value",
                "parameters": {"type": "object", "properties": {
                    "key": {"type": "string"}}, "required": ["key"],
                    "additionalProperties": False},
            },
        }]
        self.requests = []

    def test_preserves_roles_and_normalizes_tool_proposal_without_execution(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": {"key": "value"}}
        }]}}, self.requests)
        self.provider._transport = fake
        result = self.provider.generate_chat(messages=self.messages, tools=self.tools)
        request, timeout = self.requests[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["messages"], self.messages)
        self.assertEqual(result["tool_calls"]["tool_calls"][0]["name"], "lookup")
        self.assertEqual(result["tool_calls"]["tool_calls"][0]["arguments"], {"key": "value"})
        self.assertEqual(timeout, self.provider.timeout)

    def test_no_tool_choice_omits_tools(self):
        fake = FakeOpener({"message": {"content": "Hello", "tool_calls": []}}, self.requests)
        self.provider._transport = fake
        result = self.provider.generate_chat(messages=self.messages, tools=self.tools, tool_choice="none")
        self.assertNotIn("tools", json.loads(self.requests[0][0].data))
        self.assertEqual(result["text"], "Hello")

    def test_invalid_transcripts_and_tool_definitions_fail_before_network(self):
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=[{"role": [], "content": "x"}], tools=[])
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=self.messages, tools=[{}] * 33)
        with self.assertRaises(ValueError):
            self.provider.generate_chat(messages=self.messages, tools=self.tools, tool_choice="sometimes")
        self.assertEqual(self.requests, [])

    def test_required_tool_choice_rejects_text_only_response(self):
        fake = FakeOpener({"message": {"content": "No tool", "tool_calls": []}}, self.requests)
        self.provider._transport = fake
        with self.assertRaisesRegex(RuntimeError, "required tool"):
            self.provider.generate_chat(
                messages=self.messages, tools=self.tools, tool_choice="required")

    def test_none_tool_choice_rejects_returned_tool_calls(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": {"key": "value"}}
        }]}}, self.requests)
        self.provider._transport = fake
        with self.assertRaisesRegex(RuntimeError, "tool_choice was none"):
            self.provider.generate_chat(
                messages=self.messages, tools=self.tools, tool_choice="none")

    def test_rejects_unadvertised_tool_names(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "delete_everything", "arguments": {}}
        }]}}, self.requests)
        self.provider._transport = fake
        with self.assertRaisesRegex(RuntimeError, "unrecognized"):
            self.provider.generate_chat(messages=self.messages, tools=self.tools)

    def test_rejects_malformed_tool_arguments(self):
        fake = FakeOpener({"message": {"content": "", "tool_calls": [{
            "function": {"name": "lookup", "arguments": "not-an-object"}
        }]}}, self.requests)
        self.provider._transport = fake
        with self.assertRaisesRegex(RuntimeError, "malformed tool arguments"):
            self.provider.generate_chat(messages=self.messages, tools=self.tools)



    def test_native_ndjson_stream_emits_text_deltas_and_completion(self):
        chunks = [
            {"message": {"content": "Hello "}, "done": False},
            {"message": {"content": "from Yuki"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop"},
        ]
        lines = [json.dumps(chunk) + "\n" for chunk in chunks]
        opener = FakeNDJSONOpener(lines, self.requests)
        self.provider._transport = opener
        events = list(self.provider.generate_chat_stream(messages=self.messages, tools=self.tools))
        self.assertEqual(events[:2], [
            {"type": "text_delta", "text": "Hello "},
            {"type": "text_delta", "text": "from Yuki"},
        ])
        self.assertEqual(events[-1]["response"]["text"], "Hello from Yuki")
        self.assertTrue(json.loads(self.requests[0][0].data)["stream"])
        self.assertEqual(self.requests[0][0].get_header("Accept"), "application/x-ndjson")

    def test_native_ndjson_stream_normalizes_guarded_tool_proposals(self):
        chunks = [
            {"message": {"content": "Checking", "tool_calls": []}, "done": False},
            {"message": {"content": "", "tool_calls": [{
                "function": {"name": "lookup", "arguments": {"key": "value"}}
            }]}, "done": True, "done_reason": "tool_calls"},
        ]
        opener = FakeNDJSONOpener([json.dumps(chunk) + "\n" for chunk in chunks], self.requests)
        self.provider._transport = opener
        events = list(self.provider.generate_chat_stream(messages=self.messages, tools=self.tools))
        self.assertEqual(events[-1]["response"]["tool_calls"]["tool_calls"], [{
            "id": "ollama_1", "name": "lookup", "arguments": {"key": "value"}
        }])

    def test_native_ndjson_stream_rejects_missing_done_and_unadvertised_tool(self):
        missing_done = FakeNDJSONOpener([
            json.dumps({"message": {"content": "partial"}, "done": False}) + "\n"
        ], self.requests)
        self.provider._transport = missing_done
        with self.assertRaisesRegex(RuntimeError, "final done"):
            list(self.provider.generate_chat_stream(messages=self.messages, tools=self.tools))
        bad = FakeNDJSONOpener([
            json.dumps({"message": {"tool_calls": [{
                "function": {"name": "unknown", "arguments": {}}
            }]}, "done": True}) + "\n"
        ], self.requests)
        self.provider._transport = bad
        with self.assertRaisesRegex(RuntimeError, "unrecognized"):
            list(self.provider.generate_chat_stream(messages=self.messages, tools=self.tools))


    def test_default_local_transport_is_loopback_pinned_without_dns_names(self):
        from providers.ollama import _LoopbackHTTPTransport
        self.assertIsInstance(self.provider._transport, _LoopbackHTTPTransport)
        self.assertEqual(self.provider._transport._ip, "127.0.0.1")
        self.assertEqual(self.provider._transport.port, 11434)

    def test_remote_https_uses_dns_pinned_transport_and_rejects_nonstandard_port(self):
        from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport
        remote = OllamaProvider(base_url="https://ollama.example.com", model="test-model")
        self.assertIsInstance(remote._transport, PinnedHTTPSProviderTransport)
        self.assertEqual(remote._transport.allowed_hosts, frozenset({"ollama.example.com"}))
        with self.assertRaises(ValueError):
            OllamaProvider(base_url="https://ollama.example.com:8443", model="test-model")

    def test_invalid_transport_is_rejected(self):
        with self.assertRaises(TypeError):
            OllamaProvider(model="test-model", transport=object())

    def test_basic_generation_uses_the_injected_transport(self):
        fake = FakeOpener({"response": "A local answer"}, self.requests)
        self.provider._transport = fake
        self.assertEqual(self.provider.generate("Say hello"), "A local answer")
        request, timeout = self.requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(timeout, self.provider.timeout)

    def test_availability_uses_the_injected_transport(self):
        fake = FakeOpener({"models": [{"name": "test-model:latest"}]}, self.requests)
        self.provider._transport = fake
        self.assertTrue(self.provider.is_available())
        self.assertEqual(self.requests[0][0].full_url, "http://127.0.0.1:11434/api/tags")


    def test_loopback_transport_connects_to_literal_ip_and_rejects_redirects(self):
        from providers.ollama import _LoopbackHTTPTransport
        from urllib.request import Request

        class Response:
            status = 302
            reason = "Found"
            headers = {}
            def __init__(self): self.closed = False
            def close(self): self.closed = True

        class Connection:
            def __init__(self, host, port, timeout):
                self.host, self.port, self.timeout = host, port, timeout
                self.closed = False
            def request(self, *args, **kwargs): pass
            def getresponse(self):
                self.response = Response()
                return self.response
            def close(self): self.closed = True

        created = []
        def factory(host, port, timeout):
            connection = Connection(host, port, timeout)
            created.append(connection)
            return connection

        transport = _LoopbackHTTPTransport("localhost", 11434)
        with patch("providers.ollama.http.client.HTTPConnection", side_effect=factory):
            with self.assertRaisesRegex(PinnedHTTPTransportError, "redirect"):
                transport.open(Request("http://localhost:11434/api/tags"))
        self.assertEqual(created[0].host, "127.0.0.1")
        self.assertTrue(created[0].closed)
        self.assertTrue(created[0].response.closed)

    def test_loopback_transport_rejects_request_host_escape(self):
        from providers.ollama import _LoopbackHTTPTransport
        from urllib.request import Request
        transport = _LoopbackHTTPTransport("127.0.0.1", 11434)
        with self.assertRaises(PinnedHTTPTransportError):
            transport.open(Request("http://127.0.0.2:11434/api/tags"))

if __name__ == "__main__":
    unittest.main()
