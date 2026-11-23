"""Safe read-only rendering for owner-scoped proactive alert history."""
from __future__ import annotations


def render_proactive_alerts(alert_store, *, actor_id: str, limit: int = 20) -> str:
    if not callable(getattr(alert_store, "recent", None)):
        raise TypeError("alert_store must provide recent().")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be non-empty text.")
    rows = alert_store.recent(owner_id=actor_id, limit=limit)
    lines = ["Yuki proactive alerts (read-only)"]
    if not rows:
        return "\n".join(lines + ["No alerts recorded."])
    for item in rows:
        message = str(item.get("message", "")).replace("\r", " ").replace("\n", " ")[:240]
        lines.append(
            f"{item['last_seen']} | {item['category']} | {item['subject']} | "
            f"status={item.get('status', 'active')} v{item.get('version', 1)} | "
            f"id={item.get('alert_id', 'unknown')} | occurrences={item['occurrence_count']} | {message}"
        )
    return "\n".join(lines)


def render_notification_history(alert_store, *, actor_id: str, limit: int = 20) -> str:
    if not callable(getattr(alert_store, "notification_history", None)):
        raise TypeError("alert_store must provide notification_history().")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be non-empty text.")
    rows = alert_store.notification_history(owner_id=actor_id, limit=limit)
    lines = ["Yuki notification delivery history (read-only)"]
    if not rows:
        return "\n".join(lines + ["No notification events recorded."])
    for item in rows:
        error = f" error={item['last_error_type']}" if item.get("last_error_type") else ""
        lines.append(
            f"{item['created_at']} | {item['category']} | {item['subject']} | "
            f"{item['status']} | attempts={item['attempts']}{error}"
        )
    return "\n".join(lines)
