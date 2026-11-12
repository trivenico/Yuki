"""Trusted, capability-owned human-readable approval previews.

Preview handlers are application code registered by the capability owner. They
must not call an LLM or trust summaries supplied by task payloads or clients.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from core.tool_policy import PolicyRequest


@dataclass(frozen=True)
class ApprovalPreview:
    title: str
    operation: str
    target: str
    impact: str

    def __post_init__(self) -> None:
        for name in ("title", "operation", "target", "impact"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not value.strip()
                    or len(value) > 240 or value != value.strip()
                    or any(ord(char) < 32 for char in value)):
                raise ValueError(f"{name} must be bounded, printable trusted text")


class CapabilityApprovalPreviewRegistry:
    """Allowlisted preview renderers keyed by exact capability identifier."""

    def __init__(self) -> None:
        self._renderers: dict[str, Callable[[PolicyRequest], ApprovalPreview]] = {}

    def register(
        self, capability: str,
        renderer: Callable[[PolicyRequest], ApprovalPreview],
        *, operation_type: str | None = None,
    ) -> None:
        if (not isinstance(capability, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", capability)):
            raise ValueError("capability must be a bounded identifier")
        if not callable(renderer):
            raise TypeError("renderer must be callable trusted application code")
        if operation_type is not None and (
            not isinstance(operation_type, str)
            or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", operation_type)
        ):
            raise ValueError("operation_type must be a stable bounded identifier")
        if capability in self._renderers:
            raise ValueError("a preview renderer is already registered for this capability")
        self._renderers[capability] = renderer
        if not hasattr(self, "_operation_types"):
            self._operation_types: dict[str, str] = {}
        if operation_type is not None:
            self._operation_types[capability] = operation_type

    def operation_type_for(self, capability: str) -> str | None:
        """Return the explicitly registered operation type for a capability."""
        return getattr(self, "_operation_types", {}).get(capability)

    def render(self, request: PolicyRequest) -> ApprovalPreview | None:
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        renderer = self._renderers.get(request.capability)
        if renderer is None:
            return None
        preview = renderer(request)
        if not isinstance(preview, ApprovalPreview):
            raise TypeError("capability preview renderer returned an invalid preview")
        return preview
