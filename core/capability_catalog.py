"""Honest, machine-readable capability catalog for the Yuki operator interface.

The catalog reports known integration status, not inferred feature claims. A
capability marked gated or unavailable must not be advertised as operational.
"""
from __future__ import annotations

from typing import Any

_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"id": "chat", "label": "AI chat", "status": "configured-dependent",
     "description": "Text conversations through the configured provider adapter.",
     "permission": "provider credentials and network may be required"},
    {"id": "voice-input", "label": "Voice input", "status": "partial",
     "description": "Explicit recording and transcription commands are available.",
     "permission": "microphone access only after an explicit user command"},
    {"id": "voice-wakeword", "label": "Background wake word", "status": "unavailable",
     "description": "No always-listening background loop is enabled by the wake-word status command.",
     "permission": "must remain off until an opt-in lifecycle and privacy controls exist"},
    {"id": "screen-understanding", "label": "Screen understanding", "status": "unavailable",
     "description": "No production screen-capture and OCR workflow is exposed in the guarded CLI.",
     "permission": "explicit screen-capture consent required before implementation"},
    {"id": "desktop-control", "label": "Desktop and browser control", "status": "gated",
     "description": "Only individually registered skills and explicitly gated actions may execute.",
     "permission": "least privilege, per-action approval, and audit evidence"},
    {"id": "device-diagnostics", "label": "Device diagnostics", "status": "partial",
     "description": "Read-only inventory is best-effort; platform support varies.",
     "permission": "read-only metadata; no file-content inspection"},
    {"id": "host-performance", "label": "CPU, RAM, GPU and token telemetry", "status": "partial",
     "description": "Available metrics depend on platform, driver and provider instrumentation.",
     "permission": "local read-only telemetry"},
    {"id": "self-improvement", "label": "Self-improvement", "status": "supervised",
     "description": "Validate, stage, sandbox-test and record evidence; no automatic apply or deploy.",
     "permission": "isolated sandbox and human review"},
    {"id": "automatic-production-deploy", "label": "Automatic production deployment", "status": "blocked",
     "description": "Production runtime remains blocked pending complete durable side-effect controls.",
     "permission": "not enabled"},
)


def capability_catalog() -> list[dict[str, Any]]:
    """Return fresh copies so callers cannot mutate the canonical catalog."""
    return [dict(item) for item in _CAPABILITIES]


def format_capability_catalog() -> str:
    """Render the same catalog for CLI and support diagnostics."""
    lines = ["Yuki capability catalog (declared status, not a guarantee of availability):"]
    for item in _CAPABILITIES:
        lines.append(f"- {item['label']}: {item['status']} — {item['description']}")
        lines.append(f"  Permission boundary: {item['permission']}")
    return "\n".join(lines)
