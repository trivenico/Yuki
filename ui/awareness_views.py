"""Read-only rendering for authorized operational awareness snapshots."""
from __future__ import annotations

from core.awareness import render_awareness


def render_operational_awareness(service, *, actor_id: str, current_goal: str = "") -> str:
    """Render a service snapshot without exposing task payloads or worker metadata."""
    if not callable(getattr(service, "snapshot", None)):
        raise TypeError("service must provide snapshot().")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be non-empty text.")
    result = service.snapshot(actor_id=actor_id, current_goal=current_goal)
    lines = [render_awareness(result.snapshot), "", "Recommended attention (no actions taken):"]
    if result.attention_items:
        lines.extend(f"- {item}" for item in result.attention_items)
    else:
        lines.append("No attention items in the current snapshot.")
    return "\n".join(lines)
