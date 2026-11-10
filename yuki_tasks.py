"""Local task list stored as JSON beside the application."""

import json
from datetime import datetime, timezone
from pathlib import Path

TASKS_FILE = Path(__file__).with_name("yuki_tasks.json")


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        valid = []
        seen_ids = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            task_id = item.get("id")
            # bool is a subclass of int in Python; reject it explicitly. Task IDs
            # must be positive and unique so commands cannot target ambiguous rows.
            if (not isinstance(task_id, int) or isinstance(task_id, bool)
                    or task_id <= 0 or task_id in seen_ids):
                continue
            if not isinstance(item.get("description"), str) or not isinstance(item.get("completed"), bool):
                continue
            seen_ids.add(task_id)
            valid.append(item)
        return valid
    except (OSError, json.JSONDecodeError):
        return []


def save_tasks(tasks):
    temp = TASKS_FILE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(TASKS_FILE)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def add_task(tasks, description):
    if not isinstance(description, str) or not description.strip():
        return "Please provide a task description."
    next_id = max((x["id"] for x in tasks), default=0) + 1
    tasks.append({"id": next_id, "description": description.strip()[:500], "completed": False,
                  "created_at": datetime.now(timezone.utc).isoformat()})
    if save_tasks(tasks):
        return "Task added."
    tasks.pop()
    return "Could not save the task."


def list_tasks(tasks):
    if not tasks:
        return "No tasks yet. Add one with /task add <description>."
    return "\n".join(f'{x["id"]}. [{"x" if x["completed"] else " "}] {x["description"]}' for x in tasks)


def complete_task(tasks, task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "Provide a numeric task ID."
    for item in tasks:
        if item["id"] == task_id:
            old = item["completed"]
            item["completed"] = True
            if save_tasks(tasks):
                return "Task completed."
            item["completed"] = old
            return "Could not save the task."
    return "Task ID not found."


def delete_task(tasks, task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "Provide a numeric task ID."
    index = next((i for i, x in enumerate(tasks) if x["id"] == task_id), None)
    if index is None:
        return "Task ID not found."
    removed = tasks.pop(index)
    if save_tasks(tasks):
        return "Task deleted."
    tasks.insert(index, removed)
    return "Could not save the task list."
