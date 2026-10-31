import unittest

from companion.conversation import ConversationError, ConversationSession


class FakeProvider:
    def __init__(self, answers=None, *, available=True):
        self.answers = list(answers or ["Hello there!", "I remember this session's topic."])
        self.available = available
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        value = self.answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ConversationSessionTests(unittest.TestCase):
    def test_companion_persona_and_previous_turn_are_included(self):
        provider = FakeProvider()
        session = ConversationSession(provider)
        first = session.respond("Hi Yuki")
        second = session.respond("What did I just say?")
        self.assertEqual(first.message, "Hello there!")
        self.assertEqual(second.conversation_turns, 2)
        self.assertIn('You are Yuki', provider.prompts[0])
        self.assertIn("thoughtful long-term companion", provider.prompts[0].lower())
        self.assertIn("Hi Yuki", provider.prompts[1])
        self.assertIn("Hello there!", provider.prompts[1])

    def test_history_is_session_only_and_resettable(self):
        provider = FakeProvider()
        session = ConversationSession(provider)
        session.respond("remember this only for this session")
        self.assertEqual(len(session.history()), 2)
        session.reset()
        self.assertEqual(session.history(), ())
        self.assertEqual(session.conversation_turns, 0)

    def test_failed_provider_call_does_not_commit_phantom_history(self):
        provider = FakeProvider([RuntimeError("sensitive detail")])
        session = ConversationSession(provider)
        with self.assertRaises(ConversationError):
            session.respond("hello")
        self.assertEqual(session.history(), ())

    def test_provider_unavailable_fails_without_generation(self):
        provider = FakeProvider(available=False)
        session = ConversationSession(provider)
        with self.assertRaises(ConversationError):
            session.respond("hello")
        self.assertEqual(provider.prompts, [])

    def test_invalid_and_oversized_input_rejected(self):
        session = ConversationSession(FakeProvider())
        for message in ("", "  "):
            with self.subTest(message=message), self.assertRaises(ValueError):
                session.respond(message)
        with self.assertRaises(ValueError):
            session.respond("x" * (session.MAX_USER_CHARS + 1))

    def test_history_limit_must_preserve_complete_user_assistant_pairs(self):
        with self.assertRaises(ValueError):
            ConversationSession(FakeProvider(), max_history_messages=3)

    def test_invalid_or_oversized_provider_output_not_committed(self):
        provider = FakeProvider([" "])
        session = ConversationSession(provider)
        with self.assertRaises(ConversationError):
            session.respond("hello")
        self.assertEqual(session.history(), ())

    def test_history_limit_keeps_recent_messages(self):
        provider = FakeProvider(["ok"] * 10)
        session = ConversationSession(provider, max_history_messages=4)
        for i in range(6):
            session.respond(f"message {i}")
        history = session.history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1]["content"], "ok")
        self.assertIn("message 5", history[-2]["content"])
        self.assertNotIn("message 0", provider.prompts[-1])


if __name__ == "__main__":
    unittest.main()
