"""Core agent boundary for Yuki's provider-independent architecture."""

from providers import MockProvider
from core.telemetry import Telemetry


class YukiAgent:
    """Send prompts to a selected provider without exposing provider exceptions."""

    def __init__(self, provider=None, *, telemetry=None):
        self.provider = provider if provider is not None else MockProvider()
        self.telemetry = telemetry if telemetry is not None else Telemetry(enabled=True)

    def set_provider(self, provider):
        """Replace the active adapter after validating its interface."""
        if not callable(getattr(provider, "generate", None)) or not callable(
            getattr(provider, "is_available", None)
        ):
            raise TypeError("Provider must implement generate() and is_available().")
        self.provider = provider

    def status(self):
        try:
            return "Provider is ready." if self.provider.is_available() else "Provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    def ask(self, prompt):
        """Generate a response with redacted timing/status telemetry."""
        with self.telemetry.span("provider.ask", attributes={"provider": type(self.provider).__name__}) as span:
            result = self._ask(prompt)
            ok = isinstance(result, str) and not result.endswith("provider is unavailable.") and not result.endswith("provider encountered an error.")
            if hasattr(span, "set_attribute"):
                span.set_attribute("result_ok", ok)
            elif isinstance(span, dict) and callable(span.get("set_attribute")):
                span["set_attribute"]("result_ok", ok)
            return result

    def _ask(self, prompt):
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
