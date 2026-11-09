"""Privacy-preserving, append-only local inference performance telemetry.

Only bounded measurements and model/runtime identifiers are stored. Prompts,
completions, credentials, endpoint URLs, and arbitrary provider payloads are
never accepted by this API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import re
import sqlite3
from threading import RLock
from uuid import uuid4

_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.:@/-]{0,199}$")
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1000
_MAX_BYTES = 2 ** 60


def _optional_counter(value, name, *, maximum=_MAX_DURATION_MS):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"invalid_{name}")
    return value


class InferenceMetricsStore:
    """Store actor-authorized, append-only measurements for local inference."""

    def __init__(self, database_path: str, *, authorize):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(authorize):
            raise TypeError("trusted_authorize_callback_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS inference_metrics (
                metric_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_digest TEXT NOT NULL,
                quantization TEXT,
                runtime_name TEXT NOT NULL,
                runtime_version TEXT NOT NULL,
                hardware_profile TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed','cancelled')),
                load_duration_ms INTEGER,
                time_to_first_token_ms INTEGER,
                prompt_tokens INTEGER,
                generated_tokens INTEGER,
                generation_duration_ms INTEGER,
                host_memory_before_bytes INTEGER,
                host_memory_after_bytes INTEGER,
                gpu_memory_before_bytes INTEGER,
                gpu_memory_after_bytes INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(actor_id, task_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_inference_metrics_model
                ON inference_metrics(actor_id, model_digest, created_at)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def _allowed(self, actor_id, action):
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            return False
        try:
            return self.authorize(actor_id, action) is True
        except Exception:
            return False

    def record(
        self, *, actor_id, task_id, model_id, model_digest,
        runtime_name, runtime_version, hardware_profile, outcome,
        quantization=None, load_duration_ms=None, time_to_first_token_ms=None,
        prompt_tokens=None, generated_tokens=None, generation_duration_ms=None,
        host_memory_before_bytes=None, host_memory_after_bytes=None,
        gpu_memory_before_bytes=None, gpu_memory_after_bytes=None,
    ):
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        for name, value in (("model_id", model_id), ("runtime_name", runtime_name),
                            ("runtime_version", runtime_version), ("hardware_profile", hardware_profile)):
            if not isinstance(value, str) or not value.strip() or len(value) > 160:
                raise ValueError(f"invalid_{name}")
        if not isinstance(model_digest, str) or not _DIGEST.fullmatch(model_digest):
            raise ValueError("invalid_model_digest")
        if quantization is not None and (
            not isinstance(quantization, str) or not _CODE.fullmatch(quantization)
        ):
            raise ValueError("invalid_quantization")
        if outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("invalid_outcome")
        for name, value in (
            ("load_duration_ms", load_duration_ms),
            ("time_to_first_token_ms", time_to_first_token_ms),
            ("generation_duration_ms", generation_duration_ms),
        ):
            _optional_counter(value, name)
        for name, value in (("prompt_tokens", prompt_tokens), ("generated_tokens", generated_tokens)):
            _optional_counter(value, name, maximum=10_000_000)
        for name, value in (
            ("host_memory_before_bytes", host_memory_before_bytes),
            ("host_memory_after_bytes", host_memory_after_bytes),
            ("gpu_memory_before_bytes", gpu_memory_before_bytes),
            ("gpu_memory_after_bytes", gpu_memory_after_bytes),
        ):
            _optional_counter(value, name, maximum=_MAX_BYTES)
        if time_to_first_token_ms is not None and generation_duration_ms is not None:
            if time_to_first_token_ms > generation_duration_ms:
                raise ValueError("ttft_exceeds_generation_duration")
        if not self._allowed(actor_id, "inference_metrics:write"):
            raise PermissionError("metrics recording is not authorized")
        metric_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        columns = (
            "metric_id,actor_id,task_id,model_id,model_digest,quantization,"
            "runtime_name,runtime_version,hardware_profile,outcome,load_duration_ms,"
            "time_to_first_token_ms,prompt_tokens,generated_tokens,generation_duration_ms,"
            "host_memory_before_bytes,host_memory_after_bytes,gpu_memory_before_bytes,"
            "gpu_memory_after_bytes,created_at"
        )
        values = (
            metric_id, actor_id, task_id, model_id.strip(), model_digest, quantization,
            runtime_name.strip(), runtime_version.strip(), hardware_profile.strip(), outcome,
            load_duration_ms, time_to_first_token_ms, prompt_tokens, generated_tokens,
            generation_duration_ms, host_memory_before_bytes, host_memory_after_bytes,
            gpu_memory_before_bytes, gpu_memory_after_bytes, created_at,
        )
        try:
            with self._lock, self._connect() as db:
                db.execute(
                    f"INSERT INTO inference_metrics ({columns}) VALUES ({','.join('?' for _ in values)})",
                    values,
                )
                row = db.execute("SELECT * FROM inference_metrics WHERE metric_id=?", (metric_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("metrics_already_recorded_for_task") from exc
        return dict(row)

    def summary(self, *, actor_id, model_digest, limit=1000):
        if not isinstance(model_digest, str) or not _DIGEST.fullmatch(model_digest):
            raise ValueError("invalid_model_digest")
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError("invalid_limit")
        if not self._allowed(actor_id, "inference_metrics:read"):
            raise PermissionError("metrics read is not authorized")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM inference_metrics
                   WHERE actor_id=? AND model_digest=?
                   ORDER BY created_at DESC LIMIT ?""",
                (actor_id, model_digest, limit),
            ).fetchall()
        items = [dict(row) for row in rows]

        def percentile(values, fraction):
            ordered = sorted(values)
            if not ordered:
                return None
            return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

        durations = [x["generation_duration_ms"] for x in items if x["generation_duration_ms"] is not None]
        ttft = [x["time_to_first_token_ms"] for x in items if x["time_to_first_token_ms"] is not None]
        throughput = [
            round(x["generated_tokens"] * 1000 / x["generation_duration_ms"], 3)
            for x in items
            if x["generated_tokens"] is not None and x["generation_duration_ms"] not in (None, 0)
        ]
        return {
            "model_digest": model_digest,
            "sample_count": len(items),
            "success_count": sum(x["outcome"] == "succeeded" for x in items),
            "failure_count": sum(x["outcome"] == "failed" for x in items),
            "p50_generation_duration_ms": percentile(durations, 0.50),
            "p95_generation_duration_ms": percentile(durations, 0.95),
            "p50_time_to_first_token_ms": percentile(ttft, 0.50),
            "mean_generated_tokens_per_second": round(sum(throughput) / len(throughput), 3) if throughput else None,
            "records": items,
            "automatic_routing_changed": False,
        }


class _Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.commit() if exc_type is None else self.db.rollback()
        finally:
            self.db.close()
        return False
