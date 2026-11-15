"""Lifecycle-safe registry for concrete Yuki component runtimes.

The architecture catalog describes contracts; this module binds selected
contracts to runtime instances without claiming every catalog entry is active.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from core.component_catalog import (
    ComponentContract,
    DEFAULT_COMPONENTS,
    component_dependency_order,
)


@dataclass(frozen=True)
class ComponentRuntime:
    """Concrete implementation and lifecycle hooks for one component contract."""

    contract: ComponentContract
    instance: object
    start: Callable[[], None]
    close: Callable[[], None]
    health_check: Callable[[], bool]


class ComponentRegistry:
    """Register, start, inspect and close a dependency-ordered runtime set."""

    def __init__(
        self,
        contracts: Iterable[ComponentContract] = DEFAULT_COMPONENTS,
    ) -> None:
        contract_items = tuple(contracts)
        if any(not isinstance(item, ComponentContract) for item in contract_items):
            raise TypeError("contracts must contain only ComponentContract instances")
        if any(
            not isinstance(item.component_id, str) or not item.component_id
            for item in contract_items
        ):
            raise ValueError("component contracts must have non-empty string IDs")
        self._contracts = {item.component_id: item for item in contract_items}
        if len(self._contracts) != len(contract_items):
            raise ValueError("component contract IDs must be unique")
        self._runtimes: dict[str, ComponentRuntime] = {}
        self._started: list[str] = []
        self._startup_attempted = False
        self._startup_cleanup_errors: tuple[str, ...] = ()
        self._closed = False

    def register(self, runtime: ComponentRuntime) -> None:
        if self._closed:
            raise RuntimeError("component registry is closed")
        if self._startup_attempted:
            raise RuntimeError("component registry registration is frozen after startup")
        if not isinstance(runtime, ComponentRuntime):
            raise TypeError("runtime must be a ComponentRuntime")
        if not isinstance(runtime.contract, ComponentContract):
            raise TypeError("runtime contract must be a ComponentContract")
        component_id = runtime.contract.component_id
        if not isinstance(component_id, str) or not component_id:
            raise ValueError("runtime contract must have a non-empty string component ID")
        expected = self._contracts.get(component_id)
        if expected is None or expected != runtime.contract:
            raise ValueError(f"runtime contract is not registered: {component_id}")
        if component_id in self._runtimes:
            raise ValueError(f"component runtime already registered: {component_id}")
        if runtime.instance is None:
            raise ValueError(f"component runtime instance must not be None: {component_id}")
        if not all(callable(hook) for hook in (
            runtime.start, runtime.close, runtime.health_check
        )):
            raise TypeError(f"component lifecycle hooks must be callable: {component_id}")
        self._runtimes[component_id] = runtime

    def resolve(self, component_id: str) -> object:
        if self._closed:
            raise RuntimeError("component registry is closed")
        try:
            return self._runtimes[component_id].instance
        except KeyError as exc:
            raise LookupError(f"component runtime unavailable: {component_id}") from exc

    def start_all(self) -> tuple[str, ...]:
        if self._closed:
            raise RuntimeError("component registry is closed")
        if self._startup_attempted:
            return tuple(self._started)
        registered = set(self._runtimes)
        for component_id, runtime in self._runtimes.items():
            missing = set(runtime.contract.depends_on) - registered
            if missing:
                raise RuntimeError(
                    f"{component_id} is missing runtime dependencies: {', '.join(sorted(missing))}"
                )
        ordered = component_dependency_order(
            runtime.contract for runtime in self._runtimes.values()
        )
        # Freeze only after dependency ordering succeeds. Catalog graph errors
        # happen before lifecycle execution and must not lock registration.
        self._startup_attempted = True
        try:
            for contract in ordered:
                component_id = contract.component_id
                # Include the in-flight component in rollback: start hooks may
                # acquire resources before failing.
                self._started.append(component_id)
                self._runtimes[component_id].start()
        except Exception as exc:
            self._startup_cleanup_errors = self.close()
            detail = (
                f"; cleanup failed for: {', '.join(self._startup_cleanup_errors)}"
                if self._startup_cleanup_errors else ""
            )
            raise RuntimeError(
                f"component startup failed: {component_id}{detail}"
            ) from exc
        return tuple(self._started)

    def health(self) -> dict[str, bool]:
        """Probe only started runtimes; registered-but-inactive services are not live."""
        if self._closed:
            raise RuntimeError("component registry is closed")
        results: dict[str, bool] = {}
        for component_id in self._started:
            runtime = self._runtimes[component_id]
            result = runtime.health_check()
            if not isinstance(result, bool):
                raise TypeError(
                    f"component health check must return bool: {component_id}"
                )
            results[component_id] = result
        return results

    def close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        self._closed = True
        errors: list[str] = []
        while self._started:
            component_id = self._started.pop()
            try:
                self._runtimes[component_id].close()
            except Exception:
                errors.append(component_id)
        return tuple(errors)

    @property
    def startup_cleanup_errors(self) -> tuple[str, ...]:
        """Return component IDs whose rollback cleanup failed during startup."""
        return self._startup_cleanup_errors

    @property
    def started_components(self) -> tuple[str, ...]:
        return tuple(self._started)
