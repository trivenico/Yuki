"""Core agent boundary for Yuki's provider-independent architecture."""

import logging
from providers import MockProvider

logger = logging.getLogger("yuki")


class YukiAgent:
    """Send prompts to a selected provider without exposing provider exceptions."""

    def __init__(self, provider=None):
        self.provider = provider if provider is not None else MockProvider()

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
                return "Yuki: The AI provider is unavailable."
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception as exc:
            logger.exception("Provider generation failed: %s", exc)
            return "Yuki: The provider encountered an error."

    def chat(self, user_text: str, history: list[dict] = None, system_prompt: str = "") -> str:
        if not isinstance(user_text, str) or not user_text.strip():
            return "Yuki: Please enter a message."
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable."

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if history:
                messages.extend(history[-12:])
            messages.append({"role": "user", "content": user_text.strip()})

            if callable(getattr(self.provider, "chat", None)):
                result = self.provider.chat(messages)
            else:
                prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
                result = self.provider.generate(prompt)

            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception as exc:
            logger.exception("Provider chat failed: %s", exc)
            return "Yuki: The provider encountered an error."
