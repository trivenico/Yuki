"""Atomic creation of a fresh retry run from a versioned source task."""
from __future__ import annotations

import hashlib
import json
import secrets

from automation.workflow_store import (
    WorkflowConflict,
    WorkflowRunStore,
    WorkflowTransitionError,
    _MAX_KEY_CHARS,
    _MAX_DEFINITION_BYTES,
    _bounded_json,
    _now,
)


def create_retry_from_source(
    store: WorkflowRunStore,
    *,
    source_task_id: str,
    expected_version: int,
    actor_id: str,
):
    """Validate source state/version and create its retry plus audit event atomically."""
    if not isinstance(store, WorkflowRunStore):
        raise TypeError("store must be a WorkflowRunStore.")
    if not isinstance(source_task_id, str) or not source_task_id.strip() or len(source_task_id) > 200 or "\x00" in source_task_id:
        raise ValueError("source_task_id must be non-empty bounded text.")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer.")
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
        raise ValueError("actor_id must be non-empty bounded text.")

    retry_key = f"retry:{source_task_id.strip()}:{expected_version}"
    if len(retry_key) > _MAX_KEY_CHARS:
        raise ValueError("derived retry idempotency key exceeds the store limit.")

    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute(
            "SELECT * FROM workflow_runs WHERE run_id=?", (source_task_id.strip(),)
        ).fetchone()
        if source is None:
            raise KeyError("task not found.")
        if source["version"] != expected_version:
            raise WorkflowConflict("task version changed; reload before retrying.")
        if source["state"] not in {"failed", "interrupted"}:
            raise WorkflowTransitionError("only failed or interrupted tasks can be retried.")

        definition = store._validate_definition(json.loads(source["definition_json"]))
        definition_json = _bounded_json(definition, _MAX_DEFINITION_BYTES, "definition")
        name = store._validate_name(source["workflow_name"])
        digest = hashlib.sha256((name + "\n" + definition_json).encode("utf-8")).hexdigest()
        existing = db.execute(
            "SELECT * FROM workflow_runs WHERE idempotency_key=?", (retry_key,)
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != digest:
                raise WorkflowConflict("retry idempotency key was already used for a different workflow.")
            provenance = db.execute(
                "SELECT payload_json FROM task_events WHERE task_id=? AND kind='task.created' "
                "ORDER BY sequence LIMIT 1",
                (existing["run_id"],),
            ).fetchone()
            if provenance is None:
                raise WorkflowConflict("retry idempotency key is occupied by a run without retry provenance.")
            payload = json.loads(provenance["payload_json"])
            if payload.get("retry_of") != source["run_id"] or payload.get("source_version") != source["version"]:
                raise WorkflowConflict("retry idempotency key is occupied by a different source attempt.")
            return store._row(existing)

        now = _now()
        retry_id = secrets.token_urlsafe(18)
        db.execute(
            "INSERT INTO workflow_runs "
            "(run_id, idempotency_key, request_digest, workflow_name, definition_json, "
            "state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (retry_id, retry_key, digest, name, definition_json, now, now),
        )
        store._append_task_event_in_transaction(
            db, retry_id, kind="task.created", actor_id=actor_id,
            payload={
                "name": name,
                "step_count": len(definition),
                "retry_of": source["run_id"],
                "source_state": source["state"],
                "source_version": source["version"],
                "approval_required": store._requires_approval(definition),
            },
            expected_version=0,
            now=now,
        )
        return store._row(db.execute(
            "SELECT * FROM workflow_runs WHERE run_id=?", (retry_id,)
        ).fetchone())
