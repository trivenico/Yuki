"""Provider-independent deterministic response adapter for Yuki's native core.

This is not a trained language model. It gives every application surface a
truthful, offline-safe fallback while native commands, memory, planning and
task workflows remain owned by Yuki itself.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from providers.base import BaseProvider


class NativeCoreAdapter(BaseProvider):
    """A no-network adapter that never fabricates free-form model output."""

    availability_scope = "native_core"
    name = "native"
    MAX_MESSAGES = 128
    MAX_TRANSCRIPT_CHARS = 100_000
    MAX_TOOLS = 32

    _HELP = (
        "Yuki's native core is active. It can start without an external model "
        "and keeps identity, local data, permissions and task state under Yuki's "
        "control. Deterministic local workflows are available through the "
        "supported CLI and desktop controls. General free-form language generation "
        "is not available yet; an external model can be enabled as an optional adapter."
    )

    def is_available(self) -> bool:
        """The deterministic local core is available without network access."""
        return True

    def generate(self, prompt: str) -> str:
        """Return an honest native-mode response instead of pretending to infer."""
        if not isinstance(prompt, str) or not prompt.strip():
            return "Yuki: Please enter a message."
        normalized = prompt.strip().casefold()
        if normalized in {"help", "/help", "capabilities", "what can you do"}:
            return self._HELP
        return (
            "Yuki native mode is active. General free-form language generation is "
            "not available because Yuki's own language model has not yet been built. "
            "I won't invent an answer; use the supported local commands and workflows "
            "or explicitly configure an optional model adapter."
        )

    def generate_chat(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict] = (),
        tool_choice: str = "auto",
    ) -> dict:
        """Validate the public chat boundary and always decline tool execution."""
        if (not isinstance(messages, (list, tuple)) or not messages
                or len(messages) > self.MAX_MESSAGES):
            raise ValueError(
                f"messages must be a non-empty sequence of at most {self.MAX_MESSAGES} entries."
            )
        if not isinstance(tools, (list, tuple)) or len(tools) > self.MAX_TOOLS:
            raise ValueError(f"tools must contain at most {self.MAX_TOOLS} definitions.")
        if any(not isinstance(item, Mapping) for item in tools):
            raise ValueError("each tool definition must be a mapping.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")

        user_messages: list[Mapping[str, str]] = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, Mapping) or set(item) != {"role", "content"}:
                raise ValueError("each message must contain only role and content.")
            role, content = item["role"], item["content"]
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("message role or content is invalid.")
            total_chars += len(content)
            if total_chars > self.MAX_TRANSCRIPT_CHARS:
                raise ValueError("message transcript exceeds the character limit.")
            if role == "user":
                user_messages.append(item)

        if not user_messages:
            raise ValueError("messages must contain a user message.")

        # Even tool_choice="required" cannot override the native core's
        # deliberate no-tool-execution contract.
        answer = self.generate(user_messages[-1]["content"])
        return {"text": answer, "tool_calls": {"tool_calls": []}}
