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
        """Satisfy the structured chat contract while always declining tool calls."""
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ValueError("messages must be a non-empty sequence.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if not isinstance(tool_choice, str) or tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        if tool_choice == "required":
            # This adapter deliberately has no tool execution capability. Do not
            # report a successful turn when the caller explicitly requires a tool.
            raise ValueError("native core cannot satisfy tool_choice='required'; select a tool-capable adapter.")
        user_messages = [
            item for item in messages
            if isinstance(item, Mapping) and item.get("role") == "user"
            and isinstance(item.get("content"), str)
        ]
        if not user_messages:
            raise ValueError("messages must contain a user message.")
        answer = self.generate(user_messages[-1]["content"])
        return {"text": answer, "tool_calls": {"tool_calls": []}}
