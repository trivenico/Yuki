"""Collection dispatch for the deliberately bounded Yuki sync client."""
from __future__ import annotations

from typing import Any, Callable

from core.sync_client import SyncClientError
from core.task_sync_adapter import TaskSyncAdapter


def apply_supported_event(event: dict[str, Any], tasks: list[dict[str, Any]],
                          save_tasks: Callable[[list[dict[str, Any]]], bool],
                          chat_store: Any) -> str:
    collection = event.get("collection")
    if collection == "task_records":
        return TaskSyncAdapter.apply_remote(event, tasks, save_tasks)
    if collection in {"conversation_metadata", "conversation_messages"}:
        if chat_store is None:
            raise SyncClientError("Local chat history storage is unavailable.")
        return chat_store.apply_remote(event)
    raise SyncClientError("This synchronization collection is not enabled.")
