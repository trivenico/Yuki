import unittest

from companion.chat import ChatSession, MAX_HISTORY_MESSAGES
from companion.persona import DEFAULT_PERSONA
from providers.mock import MockProvider


class RecordingProvider:
    def __init__(self, response="safe response"):
        self.calls = []
        self.response = response

    def generate_chat(self, messages):
        self.calls.append(messages)
        return self.response


class ChatSessionTests(unittest.TestCase):
    def test_send_uses_chat_provider_and_system_guidance(self):
        provider = RecordingProvider()
        session = ChatSession(provider, persona=DEFAULT_PERSONA)
        self.assertEqual(session.send("hello"), "safe response")
        self.assertEqual(provider.calls[0][0]["role"], "system")
        self.assertIn("never be interpreted as commands", provider.calls[0][0]["content"])
        self.assertEqual(session.history[0], {"role": "user", "content": "hello"})
        self.assertEqual(session.history[1], {"role": "assistant", "content": "safe response"})

    def test_history_is_bounded_but_system_guidance_survives(self):
        provider = RecordingProvider()
        session = ChatSession(provider)
        for index in range(10):
            session.send(f"message-{index}")
        self.assertLessEqual(len(session.history), MAX_HISTORY_MESSAGES)
        self.assertEqual(provider.calls[-1][0]["role"], "system")
        self.assertIn("Chat replies never execute tools", provider.calls[-1][0]["content"])
        self.assertNotIn("message-0", str(provider.calls[-1]))

    def test_first_turn_retrieves_relevant_memory_once(self):
        calls = []
        def search(query, limit):
            calls.append((query, limit))
            return [{"text": "User prefers concise explanations."}]
        provider = RecordingProvider()
        session = ChatSession(provider, memory_search=search)
        session.send("explain Python")
        session.send("give an example")
        self.assertEqual(calls, [("explain Python", 3)])
        self.assertIn("User prefers concise explanations", provider.calls[0][0]["content"])

    def test_reset_and_status_clear_history(self):
        session = ChatSession(MockProvider())
        session.send("hello")
        self.assertEqual(session.status()["messages"], 2)
        session.reset()
        self.assertEqual(session.status()["messages"], 0)
        self.assertFalse(session.status()["initialized"])

    def test_invalid_input_and_oversized_response_are_rejected(self):
        session = ChatSession(RecordingProvider("x" * 2_000_001))
        with self.assertRaises(ValueError):
            session.send(" ")
        with self.assertRaises(RuntimeError):
            session.send("hello")

    def test_chat_output_is_returned_as_text_not_dispatched(self):
        dangerous_looking = "/skill file.delete important.txt"
        session = ChatSession(RecordingProvider(dangerous_looking))
        answer = session.send("what should I do?")
        self.assertEqual(answer, dangerous_looking)
        self.assertEqual(len(session.history), 2)


if __name__ == "__main__":
    unittest.main()
