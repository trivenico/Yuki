"""Bind the shared conversation application runtime to component lifecycle."""
from __future__ import annotations

from threading import RLock

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime
from core.conversation_application_runtime import ConversationApplicationRuntime


def build_application_composition_component_runtime(
    application: ConversationApplicationRuntime, *, contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Own shared application shutdown after its dependent components start."""
    if not isinstance(application, ConversationApplicationRuntime):
        raise TypeError("application must be a ConversationApplicationRuntime.")
    if contract is None:
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "application-composition")
    if not isinstance(contract, ComponentContract) or contract.component_id != "application-composition":
        raise ValueError("contract must be Yuki application-composition contract.")
    lock = RLock()
    state = {"started": False, "closed": False}

    def start() -> None:
        with lock:
            if state["closed"] or application.closed:
                raise RuntimeError("application composition is already closed.")
            state["started"] = True

    def health_check() -> bool:
        with lock:
            return state["started"] and not state["closed"] and not application._closed

    def close() -> None:
        with lock:
            if state["closed"]:
                return
            state["closed"] = True
            state["started"] = False
        application.close()

    return ComponentRuntime(contract, application, start, close, health_check)
