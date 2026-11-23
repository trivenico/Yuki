"""Authenticated, replay-resistant runtime inventory from a host-managed probe.

The probe is an operator-managed local agent or signed image/SBOM service. This
adapter never executes code in a task container. It authenticates a canonical
JSON envelope with an operator-provisioned HMAC key, binds it to the exact
container/task/worker/image/spec, enforces freshness and delegates atomic
one-time nonce consumption to a durable host-side replay guard.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from automation.agent_execution_spec import AgentExecutionSpec
from core.host_runtime_inventory import RuntimeInventoryError
from core.sandbox_executor import SandboxExecutionProfile

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_ENVELOPE_BYTES = 32_768
_MAX_ITEMS = 128


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeInventoryError("runtime inventory envelope is not canonical JSON.") from exc
    if len(encoded) > _MAX_ENVELOPE_BYTES:
        raise RuntimeInventoryError("runtime inventory envelope exceeds its byte ceiling.")
    return encoded


class AuthenticatedRuntimeInventoryProvider:
    """Verify a signed host-agent inventory without trusting task-controlled data.

    probe(task_id, worker_id, container_id, image_ref, spec_id) must contact a
    fixed local host agent using authenticated IPC and return its envelope.
    consume_nonce(source_id, nonce, expires_at_epoch) must atomically reject
    previously consumed nonces in durable host-owned storage.
    """

    def __init__(
        self,
        *,
        probe: Callable[[str, str, str, str, str], Mapping[str, Any]],
        consume_nonce: Callable[[str, str, int], bool],
        hmac_key: bytes,
        source_id: str,
        clock: Callable[[], float] = time.time,
        maximum_age_seconds: int = 60,
        maximum_clock_skew_seconds: int = 5,
    ) -> None:
        if not callable(probe) or not callable(consume_nonce):
            raise TypeError("trusted probe and durable nonce consumer are required.")
        if type(hmac_key) is not bytes or len(hmac_key) < 32:
            raise ValueError("runtime inventory HMAC key must contain at least 256 bits.")
        if type(source_id) is not str or not _SOURCE.fullmatch(source_id):
            raise ValueError("runtime inventory source_id is invalid.")
        if type(maximum_age_seconds) is not int or not 1 <= maximum_age_seconds <= 300:
            raise ValueError("maximum_age_seconds must be between 1 and 300.")
        if type(maximum_clock_skew_seconds) is not int or not 0 <= maximum_clock_skew_seconds <= 30:
            raise ValueError("maximum_clock_skew_seconds must be between 0 and 30.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._probe = probe
        self._consume_nonce = consume_nonce
        self._key = bytes(hmac_key)
        self.source_id = source_id
        self._clock = clock
        self.maximum_age_seconds = maximum_age_seconds
        self.maximum_clock_skew_seconds = maximum_clock_skew_seconds

    @staticmethod
    def _validate_inventory(name: str, value: object, *, runtime: bool) -> dict[str, str]:
        if not isinstance(value, Mapping) or len(value) > _MAX_ITEMS:
            raise RuntimeInventoryError(f"runtime inventory {name} must be a bounded mapping.")
        result: dict[str, str] = {}
        for key, version in value.items():
            if type(key) is not str or type(version) is not str or not key or len(key) > 128 or not version or len(version) > 64:
                raise RuntimeInventoryError(f"runtime inventory {name} contains invalid entries.")
            if any(ord(ch) < 33 or ord(ch) > 126 for ch in key + version):
                raise RuntimeInventoryError(f"runtime inventory {name} contains non-printable values.")
            normalized = key.lower() if runtime else re.sub(r"[-_.]+", "-", key).lower()
            if normalized in result:
                raise RuntimeInventoryError(f"runtime inventory {name} contains duplicate normalized names.")
            result[normalized] = version
        return result

    def __call__(
        self,
        task_id: str,
        worker_id: str,
        container_id: str,
        container_inspect: Mapping[str, object],
        spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile,
    ) -> Mapping[str, Mapping[str, str]]:
        if (type(container_id) is not str or not _SHA256.fullmatch(container_id)
                or not isinstance(spec, AgentExecutionSpec)
                or not isinstance(profile, SandboxExecutionProfile)):
            raise RuntimeInventoryError("runtime inventory request identity is invalid.")
        if not isinstance(container_inspect, Mapping) or container_inspect.get("Id") != container_id:
            raise RuntimeInventoryError("runtime inventory request is not bound to the inspected container.")
        try:
            envelope = self._probe(task_id, worker_id, container_id, profile.image, spec.spec_id)
        except Exception as exc:
            raise RuntimeInventoryError("trusted host runtime probe request failed.") from exc
        required = {
            "source_id", "task_id", "worker_id", "container_id", "image_ref",
            "spec_id", "issued_at", "expires_at", "nonce", "runtime_versions",
            "installed_dependencies", "signature",
        }
        if not isinstance(envelope, Mapping) or set(envelope) != required:
            raise RuntimeInventoryError("host runtime probe returned an invalid signed envelope.")
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        signature = envelope.get("signature")
        if type(signature) is not str or not _SHA256.fullmatch(signature):
            raise RuntimeInventoryError("host runtime probe signature is malformed.")
        expected = hmac.new(self._key, _canonical_bytes(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeInventoryError("host runtime probe signature verification failed.")
        bindings = {
            "source_id": self.source_id, "task_id": task_id, "worker_id": worker_id,
            "container_id": container_id, "image_ref": profile.image, "spec_id": spec.spec_id,
        }
        if any(envelope.get(key) != value for key, value in bindings.items()):
            raise RuntimeInventoryError("signed runtime inventory is bound to a different execution identity.")
        issued_at, expires_at = envelope.get("issued_at"), envelope.get("expires_at")
        if type(issued_at) is not int or type(expires_at) is not int or expires_at <= issued_at:
            raise RuntimeInventoryError("signed runtime inventory timestamps are invalid.")
        now = self._clock()
        if type(now) not in (int, float) or isinstance(now, bool):
            raise RuntimeInventoryError("trusted clock returned an invalid timestamp.")
        if issued_at > now + self.maximum_clock_skew_seconds:
            raise RuntimeInventoryError("signed runtime inventory was issued in the future.")
        if now - issued_at > self.maximum_age_seconds or expires_at <= now:
            raise RuntimeInventoryError("signed runtime inventory is stale or expired.")
        if expires_at - issued_at > self.maximum_age_seconds:
            raise RuntimeInventoryError("signed runtime inventory validity exceeds the configured limit.")
        nonce = envelope.get("nonce")
        if type(nonce) is not str or not _NONCE.fullmatch(nonce):
            raise RuntimeInventoryError("signed runtime inventory nonce is invalid.")
        try:
            consumed = self._consume_nonce(self.source_id, nonce, expires_at)
        except Exception as exc:
            raise RuntimeInventoryError("runtime inventory replay guard failed.") from exc
        if consumed is not True:
            raise RuntimeInventoryError("signed runtime inventory nonce was already consumed.")
        runtimes = self._validate_inventory("runtime_versions", envelope["runtime_versions"], runtime=True)
        packages = self._validate_inventory("installed_dependencies", envelope["installed_dependencies"], runtime=False)
        return {"runtime_versions": runtimes, "installed_dependencies": packages}


def sign_runtime_inventory_envelope(unsigned_envelope: Mapping[str, Any], hmac_key: bytes) -> str:
    """Helper for trusted host-agent implementations and deterministic tests."""
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise ValueError("runtime inventory HMAC key must contain at least 256 bits.")
    if not isinstance(unsigned_envelope, Mapping) or "signature" in unsigned_envelope:
        raise ValueError("unsigned runtime inventory envelope is required.")
    return hmac.new(hmac_key, _canonical_bytes(unsigned_envelope), hashlib.sha256).hexdigest()


class SQLiteRuntimeInventoryNonceStore:
    """Durable, transactionally atomic replay guard for signed host inventories.

    The database path is hardened with Yuki's private-SQLite path helper.
    Expired nonce rows are pruned in the same write transaction that reserves
    a new nonce. A unique constraint fences concurrent duplicate submissions.
    """

    def __init__(self, database_path: str) -> None:
        import sqlite3
        from core.sqlite_file_security import prepare_private_sqlite_path

        if type(database_path) is not str or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runtime_inventory_nonces (
                    source_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at > 0),
                    consumed_at INTEGER NOT NULL CHECK(consumed_at > 0),
                    PRIMARY KEY(source_id, nonce)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_inventory_nonce_expiry "
                "ON runtime_inventory_nonces(expires_at)"
            )

    def _connect(self):
        import sqlite3
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.database_path, timeout=5.0)
            try:
                db.execute("PRAGMA busy_timeout=5000")
                db.execute("PRAGMA synchronous=FULL")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        return connection()

    def consume(self, source_id: str, nonce: str, expires_at: int, *, now: int | None = None) -> bool:
        import sqlite3
        current = int(time.time()) if now is None else now
        if (type(source_id) is not str or not _SOURCE.fullmatch(source_id)
                or type(nonce) is not str or not _NONCE.fullmatch(nonce)
                or type(expires_at) is not int or expires_at <= current
                or type(current) is not int or current <= 0):
            raise ValueError("runtime inventory nonce reservation is invalid.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM runtime_inventory_nonces WHERE expires_at<=?", (current,))
            try:
                db.execute(
                    "INSERT INTO runtime_inventory_nonces(source_id, nonce, expires_at, consumed_at) VALUES(?,?,?,?)",
                    (source_id, nonce, expires_at, current),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def __call__(self, source_id: str, nonce: str, expires_at: int) -> bool:
        return self.consume(source_id, nonce, expires_at)
