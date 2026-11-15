"""Bind the concrete identity/access service to Yuki's component lifecycle."""
from __future__ import annotations

from threading import RLock

from core.auth import BuiltInAuthService
from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime


def build_identity_access_component_runtime(
    auth_service: BuiltInAuthService,
    *,
    contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Create a lifecycle binding backed by the auth database health probe.

    The service opens short-lived SQLite connections per operation, so the
    adapter owns no persistent connection and shutdown only fences this binding.
    """
    if not isinstance(auth_service, BuiltInAuthService):
        raise TypeError("auth_service must be a BuiltInAuthService instance.")
    health_probe = getattr(auth_service, "health_check", None)
    if not callable(health_probe):
        raise TypeError("auth_service must implement health_check().")

    if contract is None:
        contract = next(
            (item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access"),
            None,
        )
    if not isinstance(contract, ComponentContract) or contract.component_id != "identity-access":
        raise ValueError("contract must be Yuki's identity-access component contract.")

    lock = RLock()
    state = {"started": False, "closed": False}

    def check_health() -> bool:
        with lock:
            if not state["started"] or state["closed"]:
                return False
        result = health_probe()
        if type(result) is not bool:
            raise TypeError("identity/access health_check() must return bool.")
        return result

    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("identity/access component is closed.")
            result = health_probe()
            if type(result) is not bool:
                raise TypeError("identity/access health_check() must return bool.")
            if not result:
                raise RuntimeError("identity/access storage is unavailable.")
            state["started"] = True

    def close() -> None:
        with lock:
            state["closed"] = True
            state["started"] = False

    return ComponentRuntime(
        contract=contract,
        instance=auth_service,
        start=start,
        close=close,
        health_check=check_health,
    )
