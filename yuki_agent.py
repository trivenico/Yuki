"""Core agent boundary for Yuki's provider-independent architecture."""

import logging
from providers import MockProvider

logger = logging.getLogger("yuki")


class YukiAgent:
    """Send prompts to a selected provider without exposing provider exceptions."""

    def __init__(self, provider=None, usage_tracker=None):
        self.provider = provider if provider is not None else MockProvider()
        self.usage_tracker = usage_tracker

    def _record_usage(self, prompt, response):
        if self.usage_tracker is None:
            return
        usage = getattr(self.provider, "last_usage", {})
        input_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        exact = (isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0
                 and isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0)
        if not exact:
            input_tokens, output_tokens = max(1, len(prompt) // 4), max(1, len(response) // 4)
        try:
            self.usage_tracker.record(provider=type(self.provider).__name__,
                model=str(getattr(self.provider, "model", "")), input_tokens=input_tokens,
                output_tokens=output_tokens, estimated=not exact)
        except Exception:
            logger.exception("Token usage accounting failed safely.")

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
            self._record_usage(prompt.strip(), result)
            return result
        except Exception as exc:
            # Provider exceptions can contain request URLs or credential metadata.
            logger.error("Provider generation failed (%s).", type(exc).__name__)
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
            self._record_usage(user_text, result)
            return result
        except Exception as exc:
            # Avoid logging raw provider exceptions, which may include sensitive request data.
            logger.error("Provider chat failed (%s).", type(exc).__name__)
            return "Yuki: The provider encountered an error."
