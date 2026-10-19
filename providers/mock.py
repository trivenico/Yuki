from .base import BaseProvider


class MockProvider(BaseProvider):
    """Predictable local test provider; never calls an AI service."""

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            return "Mock provider: Please provide a prompt."
        return f"Mock provider received your prompt: {prompt.strip()}"

    def is_available(self) -> bool:
        return True

    def chat(self, messages: list[dict]) -> str:
        if not isinstance(messages, list) or not messages:
            return "Mock provider: Please provide chat messages."
        last_msg = messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""
        return f"Mock provider chat reply: {last_msg.strip()}"
