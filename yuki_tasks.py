"""Local task list with transactional SQLite storage and legacy JSON migration."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from yuki_sqlite_store import SQLiteJsonStore

TASKS_FILE = Path(__file__).with_name("yuki_tasks.json")
TASKS_DB_FILE = TASKS_FILE.with_suffix(".sqlite3")
_TASKS_KEY = "tasks"


def _valid_tasks(value):
    if not isinstance(value, list):
        return []
    return [
        item for item in value
        if isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and not isinstance(item.get("id"), bool)
        and isinstance(item.get("description"), str)
        and isinstance(item.get("completed"), bool)
    ]


def _store():
    return SQLiteJsonStore(TASKS_DB_FILE)


def load_tasks():
    """Load SQLite state, migrating a valid legacy JSON list once when present."""
    try:
        if TASKS_DB_FILE.exists():
            stored = _store().read_json(_TASKS_KEY, None)
            if stored is not None:
                return _valid_tasks(stored)

        if not TASKS_FILE.exists():
            return []
        try:
            legacy = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Do not overwrite or delete malformed legacy data automatically.
            return []
        if not isinstance(legacy, list):
            return []
        tasks = _valid_tasks(legacy)
        _store().write_json(_TASKS_KEY, tasks)
        return tasks
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return []


def save_tasks(tasks):
    """Replace the task list transactionally; return False on storage failure."""
    try:
        _store().write_json(_TASKS_KEY, _valid_tasks(tasks))
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


def _sync_tasks(target, updated):
    target[:] = updated


def add_task(tasks, description):
    if not isinstance(description, str) or not description.strip():
        return "Please provide a task description."
    new_task = {
        "description": description.strip()[:500],
        "completed": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store = _store()

        def append_task(current):
            current = _valid_tasks(current)
            next_id = max((item["id"] for item in current), default=0) + 1
            current.append({"id": next_id, **new_task})
            return current

        updated = store.update_json(_TASKS_KEY, append_task, default=load_tasks())
        _sync_tasks(tasks, updated)
        return "Task added."
    except (OSError, sqlite3.Error, ValueError, TypeError):
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
    found = False
    try:
        store = _store()

        def mark_completed(current):
            nonlocal found
            current = _valid_tasks(current)
            for item in current:
                if item["id"] == task_id:
                    found = True
                    item["completed"] = True
                    break
            return current

        updated = store.update_json(_TASKS_KEY, mark_completed, default=load_tasks())
        _sync_tasks(tasks, updated)
        return "Task completed." if found else "Task ID not found."
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return "Could not save the task."


def delete_task(tasks, task_id):
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "Provide a numeric task ID."
    removed = False
    try:
        store = _store()

        def remove_task(current):
            nonlocal removed
            current = _valid_tasks(current)
            updated = []
            for item in current:
                if item["id"] == task_id and not removed:
                    removed = True
                    continue
                updated.append(item)
            return updated

        updated = store.update_json(_TASKS_KEY, remove_task, default=load_tasks())
        _sync_tasks(tasks, updated)
        return "Task deleted." if removed else "Task ID not found."
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return "Could not save the task list."
