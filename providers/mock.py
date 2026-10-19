from .base import BaseProvider


class MockProvider(BaseProvider):
    """Predictable local test provider; never calls an AI service."""

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            return "Mock provider: Please provide a prompt."
        return f"Mock provider received your prompt: {prompt.strip()}"

    def generate_chat(self, messages):
        """Deterministic chat method for tests and offline development."""
        if not isinstance(messages, list) or not messages:
            raise ValueError("Messages must be a non-empty list.")
        last_user = next((item.get("content", "") for item in reversed(messages)
                          if isinstance(item, dict) and item.get("role") == "user"), "")
        if not isinstance(last_user, str):
            raise ValueError("User message must be text.")
        return f"Mock chat response: {last_user[:1000]}"

    def is_available(self) -> bool:
        return True
