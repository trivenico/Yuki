"""Core agent boundary for Yuki's provider-independent architecture."""

class YukiAgent:
    """Send prompts to a selected provider without exposing provider exceptions."""

    def __init__(self, provider=None):
        self.provider = None
        if provider is not None:
            self.set_provider(provider)

    def set_provider(self, provider):
        """Replace the active adapter after validating its interface."""
        if provider is None:
            self.provider = None
            return
        if not callable(getattr(provider, "generate", None)) or not callable(
            getattr(provider, "is_available", None)
        ):
            raise TypeError("Provider must implement generate() and is_available().")
        self.provider = provider

    def status(self):
        if self.provider is None:
            return "Yuki native core is ready; no optional model provider is configured."
        try:
            return "Optional provider is ready." if self.provider.is_available() else "Optional provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        if self.provider is None:
            return (
                "Yuki native mode is active. Local commands, memory, and task workflows "
                "remain available; configure an optional model provider for free-form generation."
            )
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable."
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception:
            return "Yuki: The provider encountered an error."
