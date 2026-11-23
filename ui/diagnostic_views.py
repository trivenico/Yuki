"""Safe, read-only diagnostics for the local Yuki supervision dashboard."""
from __future__ import annotations

import importlib.util
import platform
import sys

from core.readiness import build_readiness_report


def render_diagnostics(*, readiness_builder=build_readiness_report, module_finder=importlib.util.find_spec) -> str:
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
    lines.extend([
        "",
        "Diagnostics are read-only. No packages are installed, services started,",
        "configuration changed, or model requests sent by this report.",
    ])
    return "\n".join(lines)


def _module_available(name: str, finder) -> bool:
    try:
        return finder(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
