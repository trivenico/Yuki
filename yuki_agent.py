"""Core agent boundary for Yuki's provider-independent architecture."""
from __future__ import annotations

import os

from providers.factory import create_provider


class YukiAgent:
    """Send prompts to a configured real provider without implicit mock replies.

    Tests and callers may inject a provider explicitly. Runtime callers that do
    not inject one get the configured real provider; an unavailable provider is
    reported as unavailable rather than replaced by fabricated model output.
    """

    def __init__(self, provider=None, *, config=None, environ=None):
        env = os.environ if environ is None else environ
        self.provider = provider if provider is not None else create_provider(config, environ=env)

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
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable. Configure the selected provider; no mock fallback was used."
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception as exc:
            # Avoid leaking credentials, request bodies, or provider error payloads.
            return f"Yuki: The provider encountered an error ({type(exc).__name__})."
