"""Local task list stored as JSON beside the application.

Mutations use an inter-process lock and compare the caller's snapshot with the
latest persisted list. A stale caller is rejected rather than overwriting a
concurrent update.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from core.json_file_store import atomic_write_json, file_lock

TASKS_FILE = Path(__file__).with_name("yuki_tasks.json")


def _load_tasks_unlocked():
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Task store is unreadable; no data was overwritten.") from exc
    if not isinstance(data, list):
        raise RuntimeError("Task store has an invalid structure.")
    seen_ids = set()
    for item in data:
        if (
            not isinstance(item, dict)
            or type(item.get("id")) is not int
            or item["id"] < 1
            or not isinstance(item.get("description"), str)
            or not isinstance(item.get("completed"), bool)
        ):
            raise RuntimeError("Task store contains an invalid task; no data was overwritten.")
        if item["id"] in seen_ids:
            raise RuntimeError("Task store contains duplicate task IDs; no data was overwritten.")
        seen_ids.add(item["id"])
    return data


def load_tasks():
    with file_lock(TASKS_FILE):
        return _load_tasks_unlocked()


def save_tasks(tasks, *, expected_snapshot):
    """Replace the list only if the caller's previously loaded snapshot is current.

    Requiring an explicit snapshot prevents this legacy helper from silently
    overwriting a concurrent writer's newer task list.
    """
    if not isinstance(tasks, list) or not isinstance(expected_snapshot, list):
        return False
    with file_lock(TASKS_FILE):
        try:
            current = _load_tasks_unlocked()
            if not _matches_current_snapshot(expected_snapshot, current):
                return False
            # Reuse the same strict validation before publishing caller data.
            for item in tasks:
                if (
                    not isinstance(item, dict)
                    or type(item.get("id")) is not int
                    or item["id"] < 1
                    or not isinstance(item.get("description"), str)
                    or not isinstance(item.get("completed"), bool)
                ):
                    return False
            ids = [item["id"] for item in tasks]
            if len(ids) != len(set(ids)):
                return False
            atomic_write_json(TASKS_FILE, tasks)
            return True
        except (OSError, RuntimeError, TypeError, ValueError):
            return False


def _matches_current_snapshot(tasks, current):
    return isinstance(tasks, list) and tasks == current


def add_task(tasks, description):
    if not isinstance(description, str) or not description.strip():
        return "Please provide a task description."
    with file_lock(TASKS_FILE):
        try:
            current = _load_tasks_unlocked()
            if not _matches_current_snapshot(tasks, current):
                return "Task list changed; reload it before applying this update."
            next_id = max((item["id"] for item in current), default=0) + 1
            item = {
                "id": next_id,
                "description": description.strip()[:500],
                "completed": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            updated = [*current, item]
            atomic_write_json(TASKS_FILE, updated)
            tasks[:] = updated
            return "Task added."
        except (OSError, RuntimeError, TypeError, ValueError):
            return "Could not save the task."


def list_tasks(tasks):
    if not tasks:
        return "No tasks yet. Add one with /task add <description>."
    return "\n".join(
        f'{item["id"]}. [{"x" if item["completed"] else " "}] {item["description"]}'
        for item in tasks
    )


def complete_task(tasks, task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "Provide a numeric task ID."
    with file_lock(TASKS_FILE):
        try:
            current = _load_tasks_unlocked()
            if not _matches_current_snapshot(tasks, current):
                return "Task list changed; reload it before applying this update."
            item = next((entry for entry in current if entry["id"] == task_id), None)
            if item is None:
                return "Task ID not found."
            item["completed"] = True
            atomic_write_json(TASKS_FILE, current)
            tasks[:] = current
            return "Task completed."
        except (OSError, RuntimeError, TypeError, ValueError):
            return "Could not save the task."


def delete_task(tasks, task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "Provide a numeric task ID."
    with file_lock(TASKS_FILE):
        try:
            current = _load_tasks_unlocked()
            if not _matches_current_snapshot(tasks, current):
                return "Task list changed; reload it before applying this update."
            updated = [item for item in current if item["id"] != task_id]
            if len(updated) == len(current):
                return "Task ID not found."
            atomic_write_json(TASKS_FILE, updated)
            tasks[:] = updated
            return "Task deleted."
        except (OSError, RuntimeError, TypeError, ValueError):
            return "Could not save the task list."
