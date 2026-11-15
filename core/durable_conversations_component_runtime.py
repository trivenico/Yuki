"""Bind durable conversation storage and service to the component lifecycle."""
from __future__ import annotations

from threading import RLock

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime
from core.conversation_application_runtime import ConversationApplicationRuntime


def build_durable_conversations_component_runtime(
    application: ConversationApplicationRuntime, *, contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Expose the shared conversation service with a real SQLite readiness probe.

    The application-composition component owns application shutdown. This
    adapter only fences its own lifecycle and never closes shared resources.
    """
    if not isinstance(application, ConversationApplicationRuntime):
        raise TypeError("application must be a ConversationApplicationRuntime.")
    store_health = getattr(application.store, "health_check", None)
    if not callable(store_health):
        raise TypeError("conversation store must implement health_check().")
    if contract is None:
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "durable-conversations")
    if not isinstance(contract, ComponentContract) or contract.component_id != "durable-conversations":
        raise ValueError("contract must be Yuki durable-conversations contract.")
    lock = RLock()
    state = {"started": False, "closed": False}

    def probe() -> bool:
        result = store_health()
        if type(result) is not bool:
            raise TypeError("conversation store health_check() must return bool.")
        return result

    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("durable conversations component is closed.")
            try:
                if not probe():
                    raise RuntimeError("durable conversation storage is unavailable.")
            except Exception:
                # The application runtime is already constructed and owns the
                # store. If this dependency cannot start, release that partially
                # composed application even though its dependent adapter has not
                # yet entered the registry startup sequence.
                application.close()
                state["closed"] = True
                raise
            state["started"] = True

    def health_check() -> bool:
        with lock:
            if not state["started"] or state["closed"]:
                return False
            return probe()

    def close() -> None:
        with lock:
            state["closed"] = True
            state["started"] = False

    return ComponentRuntime(contract, application.conversations, start, close, health_check)
