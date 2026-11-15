"""Lifecycle binding for the owner-scoped agent-loop resolver.

The conversation service owns loops returned by this resolver. This component
owns only the resolver capability's registration/lifecycle fence; it never
closes loops independently or grants permissions to delegated execution.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable

from core.agent_execution_loop import AgentExecutionLoop
from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime


def build_owner_agent_execution_component_runtime(
    factory: Callable[[str], AgentExecutionLoop],
    *,
    contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Bind a callable owner-to-loop factory without invoking it during startup."""
    if not callable(factory):
        raise TypeError("owner agent execution factory must be callable.")

    if contract is None:
        contract = next(
            item for item in DEFAULT_COMPONENTS
            if item.component_id == "owner-agent-execution"
        )
    if (
        not isinstance(contract, ComponentContract)
        or contract.component_id != "owner-agent-execution"
    ):
        raise ValueError("contract must be Yuki's owner-agent-execution component contract.")

    lock = RLock()
    state = {"started": False, "closed": False}

    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("owner agent execution component is closed.")
            state["started"] = True

    def health_check() -> bool:
        with lock:
            return state["started"] and not state["closed"]

    def close() -> None:
        with lock:
            state["started"] = False
            state["closed"] = True

    return ComponentRuntime(contract, factory, start, close, health_check)
