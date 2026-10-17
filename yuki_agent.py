"""Core agent boundary for Yuki's provider-independent architecture."""


from providers import MockProvider


class YukiAgent:
    """Send prompts to a provider without exposing provider exceptions."""

    def __init__(self, provider=None):
        self.provider = provider if provider is not None else MockProvider()

    def status(self):
        try:
            return "Provider is ready." if self.provider.is_available() else "Provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable."
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception:
            return "Yuki: The provider encountered an error."
