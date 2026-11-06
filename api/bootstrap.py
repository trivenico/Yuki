"""Production composition root for Yuki's authenticated local application.

This bootstrap intentionally exposes no agent tools. Tool execution must be
added later through an explicit owner-scoped authorization and approval policy.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from api.runtime import ConversationRuntime, build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.config import load_config
from core.guarded_tool_runtime import GuardedToolRuntime
from core.agent_memory import AgentMemoryStore
from core.owner_profile import OwnerProfileStore
from companion.reminder_store import CompanionReminderStore
from core.device_pairing import DevicePairingService
from core.device_notification_outbox import DeviceNotificationOutbox
from core.structured_tool_protocol import StructuredToolProtocol
from providers.factory import create_provider


class _DenyAllTools:
    """Least-privilege policy used while the local application has no tools."""

    @staticmethod
    def authorize_tool(name: str) -> bool:
        return False


_MEMORY_OWNER = re.compile(r"^owner-[a-f0-9]{64}$")
_MEMORY_AGENT_NAMESPACES = {"assistant": "agent_memory", "owner_profile": "owner_profile"}
_MEMORY_ACTIONS = frozenset({
    "agent_memory:read", "agent_memory:write", "agent_memory:correct",
    "agent_memory:review", "agent_memory:retire", "agent_memory:delete",
    "owner_profile:read", "owner_profile:write", "owner_profile:delete",
})


def _authorize_companion_memory(actor_id: str, action: str, agent_id: str) -> bool:
    """Allow only authenticated-owner API operations on companion memory."""
    return (
        isinstance(actor_id, str)
        and _MEMORY_OWNER.fullmatch(actor_id) is not None
        and action in _MEMORY_ACTIONS
        and _MEMORY_AGENT_NAMESPACES.get(agent_id) == action.split(":", 1)[0]
    )


def _authorize_companion_reminders(owner_id: str, action: str) -> bool:
    return (
        isinstance(owner_id, str)
        and _MEMORY_OWNER.fullmatch(owner_id) is not None
        and action in {"create", "read", "cancel"}
    )


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
        if (not isinstance(role, str) or role not in {"system", "user", "assistant"}
                or not isinstance(content, str)):
            raise ValueError("Message role or content is invalid.")
        total += len(content)
        if total > 100_000:
            raise ValueError("Conversation transcript exceeds the size limit.")
        parts.append(f"<|{role}|>{chr(10)}{content}")
    return (chr(10) * 2).join(parts)


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
        if callable(getattr(provider, "generate_chat", None)):
            response = provider.generate_chat(
                messages=list(messages), tools=[], tool_choice="none"
            )
            if not isinstance(response, dict) or not isinstance(response.get("text"), str):
                raise RuntimeError("Configured provider returned an invalid chat response.")
            envelope = response.get("tool_calls", {"tool_calls": []})
            if envelope != {"tool_calls": []}:
                raise RuntimeError("Configured provider proposed tools in the no-tools runtime.")
            answer = response["text"]
        else:
            answer = provider.generate(_prompt_from_messages(list(messages)))
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
    memory_store = AgentMemoryStore(
        str(workspace / "companion-memory.sqlite3"),
        authorize=_authorize_companion_memory,
    )
    owner_profile_store = OwnerProfileStore(
        str(workspace / "owner-profile.sqlite3"),
        authorize=_authorize_companion_memory,
    )
    reminder_store = CompanionReminderStore(
        str(workspace / "companion-reminders.sqlite3"),
        authorize=_authorize_companion_reminders,
    )
    device_pairing = DevicePairingService(str(workspace / "paired-devices.sqlite3"))
    notification_outbox = DeviceNotificationOutbox(
        str(workspace / "device-notifications.sqlite3"), device_pairing
    )
    return build_conversation_runtime(
        config,
        auth_service=auth,
        agent_loop=agent_loop,
        tools_for_user=lambda _user: [],
        memory_store=memory_store,
        owner_profile_store=owner_profile_store,
        reminder_store=reminder_store,
        device_pairing=device_pairing,
        notification_outbox=notification_outbox,
        provider_catalog=lambda: [{
            "name": str(config.get("provider", "unknown")),
            "status": "configured",
            "tools_enabled": False,
        }],
    )
