"""Explicit, allow-listed export of local Yuki tasks into the sync outbox."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Callable

from core.sync_client import SyncClientError


class TaskSyncAdapter:
    """Build stable revisioned events; never performs network I/O.

    The caller must explicitly request queueing. Only the task description,
    completion state and creation timestamp are exported. Local integer IDs,
    arbitrary task fields and filesystem paths are never synchronized.
    """

    @staticmethod
    def preview(tasks: list[dict[str, Any]]) -> dict[str, int]:
        valid = [item for item in tasks if isinstance(item, dict)
                 and isinstance(item.get("description"), str)
                 and isinstance(item.get("completed"), bool)]
        return {"total": len(valid), "already_queued": sum(1 for x in valid if isinstance(x.get("_yuki_sync"), dict))}

    @staticmethod
    def queue(tasks: list[dict[str, Any]], device_id: str, enqueue: Callable[[dict[str, Any]], bool]) -> int:
        if not isinstance(device_id, str) or not device_id:
            raise SyncClientError("A paired device is required before queueing tasks.")
        queued = 0
        now = datetime.now(timezone.utc).isoformat()
        for item in tasks:
            if not isinstance(item, dict) or not isinstance(item.get("description"), str) or not isinstance(item.get("completed"), bool):
                continue
            sync_meta = item.get("_yuki_sync")
            if not isinstance(sync_meta, dict):
                sync_meta = {"object_id": "task_" + uuid4().hex, "revision": 0, "fingerprint": ""}
            object_id = sync_meta.get("object_id")
            if not isinstance(object_id, str) or not object_id.startswith("task_"):
                object_id = "task_" + uuid4().hex
                sync_meta = {"object_id": object_id, "revision": 0, "fingerprint": ""}
            payload = {
                "description": item["description"][:500],
                "completed": item["completed"],
                "created_at": str(item.get("created_at", ""))[:40],
            }
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
            if fingerprint == sync_meta.get("fingerprint"):
                item["_yuki_sync"] = sync_meta
                continue
            revision = int(sync_meta.get("revision", 0)) + 1
            event_id = "event_" + hashlib.sha256((object_id + ":" + str(revision)).encode("utf-8")).hexdigest()[:32]
            event = {
                "event_id": event_id, "device_id": device_id, "collection": "task_records",
                "object_id": object_id, "revision": revision, "occurred_at": now,
                "operation": "upsert", "payload": payload,
            }
            if enqueue(event):
                queued += 1
            item["_yuki_sync"] = {"object_id": object_id, "revision": revision, "fingerprint": fingerprint}
        return queued
