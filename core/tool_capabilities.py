"""Explicit capability metadata for tools exposed to Yuki's agent runtime.

This catalog is intentionally separate from dispatch until every existing tool
has been classified. Unknown tools fail closed when resolved; callers must not
infer safety from tool names or model-generated descriptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Iterable, Mapping


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ToolCapabilityError(ValueError):
    """Raised when tool capability metadata is missing or inconsistent."""


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECTING = "side_effecting"


@dataclass(frozen=True)
class ToolCapability:
    """Host-authored security classification for one registered tool."""

    effect: ToolEffect
    requires_user_confirmation: bool = False
    provider_idempotency: bool = False
    supports_reconciliation: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.effect, ToolEffect):
            raise ToolCapabilityError("effect must be a ToolEffect.")
        for field_name in (
            "requires_user_confirmation",
            "provider_idempotency",
            "supports_reconciliation",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ToolCapabilityError(f"{field_name} must be a boolean.")
        if self.effect is ToolEffect.READ_ONLY and (
            self.requires_user_confirmation
            or self.provider_idempotency
            or self.supports_reconciliation
        ):
            raise ToolCapabilityError(
                "read-only tools cannot declare side-effect execution controls."
            )


class ToolCapabilityCatalog:
    """Immutable, host-defined mapping of tool names to security capabilities."""

    def __init__(self, capabilities: Mapping[str, ToolCapability]):
        if not isinstance(capabilities, Mapping):
            raise TypeError("capabilities must be a mapping.")
        normalized: dict[str, ToolCapability] = {}
        for name, capability in capabilities.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ToolCapabilityError("tool names must be valid registered identifiers.")
            if not isinstance(capability, ToolCapability):
                raise ToolCapabilityError("every tool must have explicit ToolCapability metadata.")
            normalized[name] = capability
        self._capabilities = MappingProxyType(normalized)

    def resolve(self, tool_name: str) -> ToolCapability:
        """Return host-authored metadata; unknown tools are denied by omission."""
        if not isinstance(tool_name, str) or not _NAME.fullmatch(tool_name):
            raise ToolCapabilityError("tool name is invalid.")
        try:
            return self._capabilities[tool_name]
        except KeyError as exc:
            raise ToolCapabilityError("tool has no host-defined capability metadata.") from exc

    def names(self) -> tuple[str, ...]:
        """Return a stable, immutable list of registered capability names."""
        return tuple(sorted(self._capabilities))

    def require_effect(self, tool_name: str, effect: ToolEffect) -> ToolCapability:
        if not isinstance(effect, ToolEffect):
            raise ToolCapabilityError("effect must be a ToolEffect.")
        capability = self.resolve(tool_name)
        if capability.effect is not effect:
            raise ToolCapabilityError("tool effect does not match the required capability.")
        return capability
