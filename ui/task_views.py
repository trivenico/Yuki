"""Safe, metadata-only views over the authorized task runtime."""

from __future__ import annotations


def render_task_summaries(runtime, actor_id: str, *, limit: int = 50) -> str:
    """Render task metadata only; never include prompts, results, or payloads."""
    if runtime is None or not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("an authorized task runtime and actor are required.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    summaries = runtime.list_task_summaries(actor_id=actor_id, limit=limit)
    if not summaries:
        return "No tasks found."
    lines = ["Recent tasks:"]
    for item in summaries:
        owner = item.lease_owner or "unassigned"
        lines.append(
            f"{item.task_id} | {item.state} | {item.name} | "
            f"worker={owner} | version={item.version} | updated={item.updated_at}"
        )
    return "\n".join(lines)


def render_task_events(
    runtime, actor_id: str, task_id: str, *, after_sequence: int = 0, limit: int = 50,
) -> str:
    """Render event metadata only; event payloads may contain private task data."""
    if runtime is None or not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("an authorized task runtime and actor are required.")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        raise ValueError("task_id must be non-empty bounded text.")
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError("after_sequence must be a non-negative integer.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    events = runtime.events(
        actor_id=actor_id, task_id=task_id,
        after_sequence=after_sequence, limit=limit,
    )
    if not events:
        return "No new task events."
    lines = [f"Events for {task_id}:"]
    for event in events:
        lines.append(f"#{event.sequence} | {event.kind} | {event.created_at}")
    lines.append(f"Next cursor: {events[-1].sequence}")
    return "\n".join(lines)
