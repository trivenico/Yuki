"""Pure helpers for a safe, read-only companion briefing."""
from __future__ import annotations

from datetime import datetime


def render_morning_briefing(
    *,
    now: datetime,
    workspace: str,
    provider: str,
    skills_count: int,
    reminders: list[dict],
    voice_enabled: bool,
) -> str:
    """Render a deterministic snapshot without performing external actions."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("workspace must be a non-empty string.")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider must be a non-empty string.")
    if isinstance(skills_count, bool) or not isinstance(skills_count, int) or skills_count < 0:
        raise ValueError("skills_count must be a non-negative integer.")
    if not isinstance(reminders, list):
        raise ValueError("reminders must be a list.")
    if not isinstance(voice_enabled, bool):
        raise ValueError("voice_enabled must be a boolean.")

    pending = [item for item in reminders if isinstance(item, dict) and not item.get("delivered")]
    overdue = []
    for item in pending:
        try:
            due_at = datetime.fromisoformat(item["due_at"])
            if due_at.tzinfo is None or due_at.utcoffset() is None:
                continue
            if due_at <= now:
                overdue.append(item)
        except (KeyError, TypeError, ValueError):
            continue

    lines = [
        f"Yuki morning briefing — {now.strftime('%A, %B %d, %Y at %H:%M %Z')}",
        f"Workspace: {workspace}",
        f"Provider: {provider} (configured; live readiness is not inferred)",
        f"Skills available: {skills_count}",
        f"Reminders: {len(pending)} pending, {len(overdue)} due/overdue",
    ]
    for item in sorted(overdue, key=lambda value: value.get("due_at", ""))[:5]:
        lines.append(f"- Due: {item.get('text', 'Reminder')}")
    upcoming = [item for item in pending if item not in overdue]
    for item in sorted(upcoming, key=lambda value: value.get("due_at", ""))[:3]:
        lines.append(f"- Upcoming {item.get('due_at')}: {item.get('text', 'Reminder')}")
    lines.extend([
        "Voice: " + ("enabled by settings; capture remains explicit" if voice_enabled else "disabled in settings"),
        "Privacy: local-first configuration; cloud use requires explicit provider selection.",
        "This briefing is a read-only snapshot; it does not send messages or execute tasks.",
    ])
    return "\n".join(lines)
