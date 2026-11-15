"""Bind an immutable, validated configuration snapshot to component lifecycle."""
from __future__ import annotations

from collections.abc import Mapping
import math
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Any

from core.component_catalog import DEFAULT_COMPONENTS, ComponentContract
from core.component_registry import ComponentRuntime


def _freeze(value: Any) -> Any:
    """Freeze supported configuration primitives and reject opaque mutable values."""
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("configuration mapping keys must be strings.")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("configuration floats must be finite.")
        return value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError("configuration values must be mappings, sequences, sets or scalar primitives.")


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """Read-only configuration values for one runtime composition."""

    values: Mapping[str, Any] = field(repr=False)

    def __repr__(self) -> str:
        return f"ConfigurationSnapshot(fields={len(self.values)})"

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def build_configuration_component_runtime(
    config: Mapping[str, Any],
    *,
    contract: ComponentContract | None = None,
) -> ComponentRuntime:
    """Create a lifecycle adapter for a validated configuration snapshot.

    The adapter intentionally reports only structural health. It never emits
    configuration values, credentials, database URLs, or environment values.
    """
    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping.")
    # _freeze recursively copies supported containers into immutable forms;
    # deepcopy is unnecessary and could execute arbitrary __deepcopy__ hooks.
    snapshot_values = dict(config)
    workspace = snapshot_values.get("workspace")
    provider = snapshot_values.get("provider")
    storage_backend = snapshot_values.get("storage_backend", "sqlite")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("configuration requires a non-empty workspace.")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("configuration requires a non-empty provider.")
    if storage_backend not in {"sqlite", "postgres"}:
        raise ValueError("configuration storage_backend must be sqlite or postgres.")

    if contract is None:
        contract = next(
            item for item in DEFAULT_COMPONENTS
            if item.component_id == "configuration-runtime"
        )
    if (
        not isinstance(contract, ComponentContract)
        or contract.component_id != "configuration-runtime"
    ):
        raise ValueError("contract must be Yuki's configuration-runtime component contract.")

    snapshot = ConfigurationSnapshot(_freeze(snapshot_values))
    lock = RLock()
    state = {"started": False, "closed": False}

    def start() -> None:
        with lock:
            if state["closed"]:
                raise RuntimeError("configuration component is closed.")
            # Recheck the immutable snapshot at the lifecycle boundary.
            if (
                not snapshot.get("workspace")
                or not snapshot.get("provider")
                or snapshot.get("storage_backend", "sqlite") not in {"sqlite", "postgres"}
            ):
                raise RuntimeError("configuration snapshot is invalid.")
            state["started"] = True

    def health_check() -> bool:
        with lock:
            return (
                state["started"]
                and not state["closed"]
                and isinstance(snapshot.get("workspace"), str)
                and bool(snapshot.get("workspace").strip())
                and isinstance(snapshot.get("provider"), str)
                and bool(snapshot.get("provider").strip())
                and snapshot.get("storage_backend", "sqlite") in {"sqlite", "postgres"}
            )

    def close() -> None:
        with lock:
            state["closed"] = True
            state["started"] = False

    return ComponentRuntime(contract, snapshot, start, close, health_check)
