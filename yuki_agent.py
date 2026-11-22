"""Yuki's native-first conversational entry point.

Optional model providers may generate language, but local registered skills and
their guarded execution path remain owned by Yuki.
"""
import json


class YukiAgent:
    """Route to Yuki-native capabilities first when no model is selected."""

    def __init__(self, provider=None, native_agent=None):
        self.provider = None
        self.native_agent = None
        if native_agent is not None:
            if not callable(getattr(native_agent, "run_skill_for_task", None)):
                raise TypeError("native_agent must implement run_skill_for_task().")
            self.native_agent = native_agent
        if provider is not None:
            self.set_provider(provider)

    def set_provider(self, provider):
        """Replace the active optional adapter after validating its interface."""
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
            native = "native capabilities are enabled" if self.native_agent else "native core is ready"
            return f"Yuki {native}; no optional model provider is configured."
        try:
            return "Optional provider is ready." if self.provider.is_available() else "Optional provider is unavailable."
        except Exception:
            return "Provider status is unknown."

    def _ask_native(self, prompt):
        if self.native_agent is None:
            return None
        try:
            result = self.native_agent.run_skill_for_task(prompt, "")
        except Exception:
            return "Yuki: Native capability routing encountered a recoverable error."
        if getattr(result, "stage", "") == "intent_ambiguous":
            return "Yuki: " + str(result.message)
        if getattr(result, "stage", "") == "permission_denied":
            return "Yuki: I did not execute that capability because approval was not granted."
        if not getattr(result, "ok", False):
            if getattr(result, "tool_name", None):
                return "Yuki: " + str(result.message)
            return None
        message = str(getattr(result, "message", "Native capability completed."))
        data = getattr(result, "data", None)
        if data is not None:
            try:
                rendered = json.dumps(data, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                rendered = str(data)
            return "Yuki: " + message + "\n" + rendered
        return "Yuki: " + message

    def ask(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        cleaned = prompt.strip()
        native_response = self._ask_native(cleaned)
        if native_response is not None:
            return native_response
        if self.provider is None:
            return (
                "Yuki native mode is active. Local registered capabilities, memory, "
                "and task workflows remain available; free-form language generation "
                "requires an explicitly installed local model."
            )
        try:
            if not self.provider.is_available():
                return (
                    "Yuki: The optional model provider is unavailable. "
                    "Native local capabilities remain available."
                )
            result = self.provider.generate(cleaned)
            if not isinstance(result, str):
                return "Yuki: The provider returned an unsupported response."
            return result
        except Exception:
            return "Yuki: The optional provider encountered an error; native local capabilities remain available."
