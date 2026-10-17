from providers import MockProvider


class YukiAgent:
    """A basic agent that sends prompts to a configured provider."""

    def __init__(self, provider=None):
        self.provider = provider or MockProvider()

    def status(self):
        return "Provider is ready." if self.provider.is_available() else "Provider is unavailable."

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        if not self.provider.is_available():
            return "Yuki: The AI provider is unavailable."
        try:
            return self.provider.generate(prompt.strip())
        except Exception:
            return "Yuki: The provider encountered an error."
