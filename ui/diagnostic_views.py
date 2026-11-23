"""Safe, read-only diagnostics for the local Yuki supervision dashboard."""
from __future__ import annotations

import importlib.util
import platform
import sys

from core.readiness import build_readiness_report
from core.diagnostics import DiagnosticEventStore


def render_diagnostics(*, readiness_builder=build_readiness_report, module_finder=importlib.util.find_spec, event_store_factory=DiagnosticEventStore) -> str:
    """Render capability metadata only; never expose environment values or secrets."""
    report = readiness_builder()
    lines = [
        "Yuki developer diagnostics",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Platform: {platform.system()} / {platform.machine()}",
        f"Textual dashboard: {'available' if _module_available('textual', module_finder) else 'missing'}",
        "",
        "Detected capabilities (availability is not an inference or end-to-end test):",
    ]
    for name, capability in sorted(report.capabilities.items()):
        detail = str(capability.detail).replace("\r", " ").replace("\n", " ")[:240]
        lines.append(f"{name}: {capability.status} — {detail}")
    lines.append("")
    lines.append("Recent safe runtime events:")
    try:
        events = event_store_factory().recent(10)
        if not events:
            lines.append("No recorded runtime events.")
        for item in events:
            error = f" error={item['error_type']}" if item.get("error_type") else ""
            lines.append(
                f"{item['timestamp']} | {item['component']} | {item['event']}{error}"
            )
    except Exception as exc:
        lines.append(f"Event history unavailable ({type(exc).__name__}).")
    lines.extend([
        "",
        "Only event metadata and exception class names are retained; raw error",
        "messages, prompts, credentials and task payloads are excluded.",
        "This report is read-only and does not install packages, start services,",
        "change configuration, or send model requests.",
    ])
    return "\n".join(lines)


def _module_available(name: str, finder) -> bool:
    try:
        return finder(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
