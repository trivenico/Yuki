"""Explicit provider-backed task handlers for Yuki's bounded worker.

Task payloads select a task type and contain a prompt; they cannot select a
provider, endpoint, executable, tool, or arbitrary handler. Provider selection
is configured out-of-band through the existing ProviderManager.
"""
from __future__ import annotations

from typing import Any

from yuki_agent_worker import TaskContext


MAX_PROVIDER_PROMPT_CHARS = 12_000
MAX_PROVIDER_RESPONSE_CHARS = 50_000


def create_provider_handlers(provider_manager, *, max_prompt_chars: int = MAX_PROVIDER_PROMPT_CHARS):
    """Return the allowlisted model.generate handler using the selected provider.

    The selected adapter is resolved once at handler construction. To change
    providers, explicitly rebuild the handler mapping after policy/user approval.
    """
    if isinstance(max_prompt_chars, bool) or not isinstance(max_prompt_chars, int):
        raise TypeError("max_prompt_chars must be an integer")
    if not 1 <= max_prompt_chars <= MAX_PROVIDER_PROMPT_CHARS:
        raise ValueError(f"max_prompt_chars must be between 1 and {MAX_PROVIDER_PROMPT_CHARS}")
    if not callable(getattr(provider_manager, "create_selected_adapter", None)):
        raise TypeError("provider_manager must expose create_selected_adapter()")
    provider = provider_manager.create_selected_adapter()
    if provider is None or not callable(getattr(provider, "generate", None)) or not callable(
        getattr(provider, "is_available", None)
    ):
        raise RuntimeError("The selected provider is not configured.")
    if not provider.is_available():
        raise RuntimeError("The selected provider is not available.")

    def generate(payload: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("model.generate payload must be an object")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("model.generate requires a non-empty prompt")
        if len(prompt) > max_prompt_chars:
            raise ValueError(f"prompt exceeds the {max_prompt_chars}-character limit")
        # Do not allow per-task provider/base URL/model overrides. Those are
        # controlled by the trusted app configuration and provider manager.
        forbidden = {"provider", "model", "base_url", "endpoint", "executable", "command", "tools"}
        if forbidden.intersection(payload):
            raise ValueError("provider and tool configuration cannot be set in task payloads")
        context.check_cancelled()
        response = provider.generate(prompt)
        context.check_cancelled()
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("provider returned an empty or invalid response")
        if len(response) > MAX_PROVIDER_RESPONSE_CHARS:
            response = response[:MAX_PROVIDER_RESPONSE_CHARS] + "\n[Response truncated by Yuki.]"
        return {
            "provider": provider.__class__.__name__,
            "text": response,
            "truncated": len(response) > MAX_PROVIDER_RESPONSE_CHARS,
        }

    return {"model.generate": generate}
