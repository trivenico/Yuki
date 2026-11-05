"""Production composition root for Yuki's authenticated local application.

This bootstrap intentionally exposes no agent tools. Tool execution must be
added later through an explicit owner-scoped authorization and approval policy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from api.runtime import ConversationRuntime, build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.config import load_config
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.factory import create_provider


class _DenyAllTools:
    """Least-privilege policy used while the local application has no tools."""

    @staticmethod
    def authorize_tool(name: str) -> bool:
        return False


def _prompt_from_messages(messages: list[dict[str, str]]) -> str:
    """Preserve role boundaries for provider adapters exposing generate(prompt)."""
    if not isinstance(messages, list) or not messages or len(messages) > 128:
        raise ValueError("messages must contain 1 to 128 entries.")
    parts: list[str] = []
    total = 0
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ValueError("Each message must contain only role and content.")
        role, content = item["role"], item["content"]
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise ValueError("Message role or content is invalid.")
        total += len(content)
        if total > 100_000:
            raise ValueError("Conversation transcript exceeds the size limit.")
        parts.append(f"<|{role}|>\\n{content}")
    return "\\n\\n".join(parts)


def build_yuki_runtime(config_path: str | Path | None = None) -> ConversationRuntime:
    """Construct Yuki from real configured providers and durable local services.

    Provider credentials remain in the process environment. This function does
    not create accounts, enable tools, activate remote providers, or start a
    network listener.
    """
    config = load_config(config_path)
    workspace = Path(config["workspace"]).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    provider = create_provider(config)

    def model(*, messages, tools, tool_choice="auto") -> dict[str, Any]:
        if tools:
            raise ValueError("This runtime has no enabled tools.")
        prompt = _prompt_from_messages(list(messages))
        answer = provider.generate(prompt)
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Configured provider returned an invalid response.")
        return {"text": answer.strip(), "tool_calls": {"tool_calls": []}}

    tool_runtime = GuardedToolRuntime({}, _DenyAllTools())
    agent_loop = AgentExecutionLoop(
        model,
        StructuredToolProtocol(tool_runtime),
        max_model_turns=1,
        max_tool_calls=1,
        max_duration_seconds=120,
    )
    auth = BuiltInAuthService(str(workspace / "auth.sqlite3"))
    return build_conversation_runtime(
        config,
        auth_service=auth,
        agent_loop=agent_loop,
        tools_for_user=lambda _user: [],
        provider_catalog=lambda: [{
            "name": str(config.get("provider", "unknown")),
            "status": "configured",
            "tools_enabled": False,
        }],
    )
