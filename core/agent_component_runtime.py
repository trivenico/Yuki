"""Bind a bounded agent execution loop to the canonical component lifecycle.

The adapter owns only the loop's registry lifecycle state. Provider health and
resource ownership remain with the provider-runtime component.
"""
from __future__ import annotations

from threading import RLock

from core.agent_execution_loop import AgentExecutionLoop
from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime


def build_agent_execution_component_runtime(
    agent_loop: AgentExecutionLoop,
    *,
    contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Create a lifecycle binding for one concrete AgentExecutionLoop.

    Health reports whether this loop has been started and not shut down. It
    deliberately does not duplicate provider availability checks; the declared
    provider-runtime dependency owns that evidence.
    """
    if not isinstance(agent_loop, AgentExecutionLoop):
        raise TypeError("agent_loop must be an AgentExecutionLoop instance.")
    if not callable(getattr(agent_loop, "run", None)):
        raise TypeError("agent_loop must implement run().")

    if contract is None:
        contract = next(
            (item for item in DEFAULT_COMPONENTS if item.component_id == "agent-execution"),
            None,
        )
    if not isinstance(contract, ComponentContract) or contract.component_id != "agent-execution":
        raise ValueError("contract must be Yuki's agent-execution component contract.")

    lock = RLock()
    state = {"started": False, "closed": False}

    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("agent execution component is closed.")
            state["started"] = True

    def health_check() -> bool:
        with lock:
            return state["started"] and not state["closed"]

    def close() -> None:
        with lock:
            state["closed"] = True
            state["started"] = False

    return ComponentRuntime(
        contract=contract,
        instance=agent_loop,
        start=start,
        close=close,
        health_check=health_check,
    )
