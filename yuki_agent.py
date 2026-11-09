"""Core agent boundary for Yuki's provider-independent architecture."""

from providers import MockProvider


class YukiAgent:
    """Send prompts to a selected provider without exposing provider exceptions."""

    def __init__(self, provider=None, *, usage_tracker=None, on_usage_error=None):
        self.provider = provider if provider is not None else MockProvider()
        if usage_tracker is not None and not callable(getattr(usage_tracker, "record", None)):
            raise TypeError("usage_tracker must expose record().")
        if on_usage_error is not None and not callable(on_usage_error):
            raise TypeError("on_usage_error must be callable.")
        self.usage_tracker = usage_tracker
        self.on_usage_error = on_usage_error
        self.last_usage_error = None

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

    def _record_usage(self, prompt, response):
        if self.usage_tracker is None:
            return
        try:
            usage = getattr(self.provider, "last_usage", None)
            if (
                isinstance(usage, dict)
                and type(usage.get("input_tokens")) is int and usage["input_tokens"] >= 0
                and type(usage.get("output_tokens")) is int and usage["output_tokens"] >= 0
                and type(usage.get("estimated")) is bool
            ):
                input_tokens = usage["input_tokens"]
                output_tokens = usage["output_tokens"]
                estimated = usage["estimated"]
            else:
                input_tokens = (len(prompt) + 3) // 4
                output_tokens = (len(response) + 3) // 4
                estimated = True
            self.usage_tracker.record(
                provider=self.provider.__class__.__name__.lower(),
                model=str(getattr(self.provider, "model", "")),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated=estimated,
            )
            self.last_usage_error = None
        except Exception as exc:
            self.last_usage_error = type(exc).__name__
            if self.on_usage_error is not None:
                try:
                    self.on_usage_error(exc)
                except Exception:
                    pass

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        try:
            if not self.provider.is_available():
                return "Yuki: The AI provider is unavailable."
            # Clear any prior response metadata so an unsupported provider cannot
            # accidentally reuse the previous call's token counts.
            try:
                self.provider.last_usage = None
            except Exception:
                pass
            result = self.provider.generate(prompt.strip())
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            self._record_usage(prompt.strip(), result)
            return result
        except Exception:
            return "Yuki: The provider encountered an error."
