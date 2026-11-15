"""Bind the CLI-local owner principal to the identity/access component contract."""
from __future__ import annotations

from threading import RLock
import re

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime

_OWNER_ID = re.compile(r"^local-[A-Za-z0-9_-]{24,64}$")


def build_local_identity_access_component_runtime(owner_id: str, *, contract: ComponentContract | None = None) -> ComponentRuntime:
    """Represent a validated local-only owner scope without claiming web auth.

    The CLI owner ID is a storage namespace, not a credential or network
    principal. Permission checks remain fail-closed in the CLI tool policy.
    """
    if not isinstance(owner_id, str) or not _OWNER_ID.fullmatch(owner_id):
        raise ValueError("owner_id must be a validated local owner identity.")
    if contract is None:
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
    if not isinstance(contract, ComponentContract) or contract.component_id != "identity-access":
        raise ValueError("contract must be Yuki identity-access component contract.")
    lock = RLock()
    state = {"started": False, "closed": False}
    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("local identity component is closed.")
            state["started"] = True
    def health_check() -> bool:
        with lock:
            return state["started"] and not state["closed"] and bool(_OWNER_ID.fullmatch(owner_id))
    def close() -> None:
        with lock:
            state["closed"] = True
            state["started"] = False
    return ComponentRuntime(contract, owner_id, start, close, health_check)
