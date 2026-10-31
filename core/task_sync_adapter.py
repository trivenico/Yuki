"""Explicit, allow-listed export of local Yuki tasks into the sync outbox."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from core.sync_client import SyncClientError


class TaskSyncAdapter:
    """Build stable revisioned events; never performs network I/O.

    Only description, completion state and creation timestamp are exported.
    Local integer IDs and arbitrary task fields are never synchronized.
    """

    @staticmethod
    def preview(tasks: list[dict[str, Any]]) -> dict[str, int]:
        valid = [item for item in tasks if isinstance(item, dict)
                 and isinstance(item.get("description"), str)
                 and isinstance(item.get("completed"), bool)]
        return {"total": len(valid), "tracked": sum(1 for x in valid if isinstance(x.get("_yuki_sync"), dict))}

    @staticmethod
    def queue(tasks: list[dict[str, Any]], device_id: str, enqueue: Callable[[dict[str, Any]], bool]) -> int:
        if not isinstance(device_id, str) or not device_id:
            raise SyncClientError("A paired device is required before queueing tasks.")
        queued = 0
        for item in tasks:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
                continue
            if not isinstance(item.get("description"), str) or not isinstance(item.get("completed"), bool):
                continue
            payload = {
                "description": item["description"][:500],
                "completed": item["completed"],
                "created_at": str(item.get("created_at", ""))[:40],
            }
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
            old_meta = item.get("_yuki_sync") if isinstance(item.get("_yuki_sync"), dict) else {}
            if fingerprint == old_meta.get("fingerprint"):
                continue
            # Deterministic per-device identity makes a crash before metadata
            # persistence safe: retrying produces the same object/event IDs.
            object_id = "task_" + hashlib.sha256((device_id + ":" + str(item["id"])).encode("utf-8")).hexdigest()[:32]
            old_revision = old_meta.get("revision", 0)
            if isinstance(old_revision, bool) or not isinstance(old_revision, int) or old_revision < 0:
                old_revision = 0
            revision = old_revision + 1
            occurred_at = payload["created_at"] or "1970-01-01T00:00:00+00:00"
            event_id = "event_" + hashlib.sha256((object_id + ":" + str(revision)).encode("utf-8")).hexdigest()[:32]
            event = {
                "event_id": event_id, "device_id": device_id, "collection": "task_records",
                "object_id": object_id, "revision": revision, "occurred_at": occurred_at,
                "operation": "upsert", "payload": payload,
            }
            # Persist metadata only after the outbox accepted this exact event.
            enqueue(event)
            item["_yuki_sync"] = {"object_id": object_id, "revision": revision, "fingerprint": fingerprint}
            queued += 1
        return queued


    @staticmethod
    def apply_remote(event: dict[str, Any], tasks: list[dict[str, Any]],
                     save: Callable[[list[dict[str, Any]]], bool]) -> str:
        """Idempotently apply one remote task upsert to the local task JSON.

        Delete events and non-task collections are deliberately rejected until
        the task store has durable tombstones and a reviewed delete policy.
        """
        from copy import deepcopy
        from core.sync_contract import validate_sync_event

        normalized = validate_sync_event(event).to_dict()
        if normalized["collection"] != "task_records" or normalized["operation"] != "upsert":
            raise SyncClientError("This device currently accepts task upserts only.")
        payload = normalized["payload"]
        if set(payload) != {"description", "completed", "created_at"}:
            raise SyncClientError("Remote task payload does not match the approved task schema.")
        if not isinstance(payload["description"], str) or len(payload["description"]) > 500:
            raise SyncClientError("Remote task description is invalid.")
        if not isinstance(payload["completed"], bool):
            raise SyncClientError("Remote task completion state is invalid.")
        if not isinstance(payload["created_at"], str) or len(payload["created_at"]) > 40:
            raise SyncClientError("Remote task timestamp is invalid.")

        object_id = normalized["object_id"]
        revision = normalized["revision"]
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        existing_index = next((
            index for index, item in enumerate(tasks)
            if isinstance(item, dict)
            and isinstance(item.get("_yuki_sync"), dict)
            and item["_yuki_sync"].get("object_id") == object_id
        ), None)

        if existing_index is not None:
            current = tasks[existing_index]
            metadata = current["_yuki_sync"]
            current_revision = metadata.get("revision", 0)
            if isinstance(current_revision, bool) or not isinstance(current_revision, int):
                raise SyncClientError("Local task sync revision is invalid.")
            if revision < current_revision:
                return "stale"
            if revision == current_revision:
                if metadata.get("fingerprint") == fingerprint:
                    return "duplicate"
                raise SyncClientError("Remote task revision conflicts with local task content.")
            if revision != current_revision + 1:
                raise SyncClientError("Remote task revision gap; refusing to overwrite local data.")
            before = deepcopy(current)
            current.update({
                "description": payload["description"],
                "completed": payload["completed"],
                "created_at": payload["created_at"],
                "_yuki_sync": {
                    "object_id": object_id, "revision": revision,
                    "fingerprint": fingerprint,
                },
            })
            if not save(tasks):
                current.clear()
                current.update(before)
                raise SyncClientError("Unable to persist the remote task update.")
            return "updated"

        # A new remote task must begin at revision one. IDs remain local and
        # are never used as cross-device identities.
        if revision != 1:
            raise SyncClientError("Remote task revision gap; refusing to create an incomplete task.")
        local_id = max((item.get("id", 0) for item in tasks
                        if isinstance(item, dict) and isinstance(item.get("id"), int)
                        and not isinstance(item.get("id"), bool)), default=0) + 1
        task = {
            "id": local_id,
            "description": payload["description"],
            "completed": payload["completed"],
            "created_at": payload["created_at"],
            "_yuki_sync": {
                "object_id": object_id, "revision": revision,
                "fingerprint": fingerprint,
            },
        }
        tasks.append(task)
        if not save(tasks):
            tasks.pop()
            raise SyncClientError("Unable to persist the remote task.")
        return "created"
