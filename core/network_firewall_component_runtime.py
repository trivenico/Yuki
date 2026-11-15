"""Lifecycle adapter for Yuki's outbound network firewall policy."""
from __future__ import annotations

from threading import RLock

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime
from core.network_firewall import NetworkFirewall


def build_network_firewall_component_runtime(
    firewall: NetworkFirewall, *, contract: ComponentContract | None = None,
) -> ComponentRuntime:
    if not isinstance(firewall, NetworkFirewall):
        raise TypeError("firewall must be a NetworkFirewall.")
    if contract is None:
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "network-firewall")
    if not isinstance(contract, ComponentContract) or contract.component_id != "network-firewall":
        raise ValueError("contract must be Yuki's network-firewall component contract.")
    lock = RLock()
    state = {"started": False, "closed": False}

    def start():
        with lock:
            if state["closed"]:
                raise RuntimeError("network firewall is closed.")
            if not firewall.allowed_hosts or not firewall.allowed_ports:
                raise RuntimeError("network firewall policy is empty.")
            state["started"] = True

    def health_check():
        with lock:
            return state["started"] and not state["closed"] and bool(firewall.allowed_hosts)

    def close():
        with lock:
            state["closed"] = True
            state["started"] = False

    return ComponentRuntime(contract, firewall, start, close, health_check)
