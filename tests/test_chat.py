import unittest

from providers.mock import MockProvider
from yuki_agent import YukiAgent


class ChatLoopTests(unittest.TestCase):
    def test_mock_provider_chat_and_history(self):
        agent = YukiAgent(MockProvider())
        reply = agent.chat("Hello Yuki!", system_prompt="You are a helpful assistant")
        self.assertIn("Mock provider chat reply: Hello Yuki!", reply)

    def test_chat_maintains_history_bounds(self):
        agent = YukiAgent(MockProvider())
        history = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        reply = agent.chat("Latest message", history=history)
        self.assertIn("Latest message", reply)


if __name__ == "__main__":
    unittest.main()
