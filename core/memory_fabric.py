"""Bounded tiered cache for reconstructible Yuki working-set data.

This is an SSD-backed cache, not virtual RAM. It stores bytes only and never
unpickles or otherwise deserializes arbitrary Python objects.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import sqlite3
from contextlib import contextmanager
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable


class MemoryFabricError(RuntimeError):
    """Base error for Yuki Memory Fabric."""


class MemoryFabricCorruption(MemoryFabricError):
    """Raised when an SSD cache entry fails its integrity check."""


class MemoryFabricClosed(MemoryFabricError):
    """Raised when an operation is attempted after close()."""


class MemoryFabric:
    """A per-process RAM LRU in front of a bounded SQLite SSD cache.

    Only reconstructible byte payloads belong here. Authoritative knowledge,
    credentials, approval state, and audit records must use their own stores.
    """

    MAX_NAMESPACE_LENGTH = 128
    MAX_KEY_LENGTH = 512
    MAX_ENTRY_BYTES = 64 * 1024 * 1024
    MAX_TTL_SECONDS = 30 * 24 * 60 * 60

    def __init__(
        self,
        path: str | Path,
        *,
        ram_capacity_bytes: int = 64 * 1024 * 1024,
        disk_capacity_bytes: int = 1024 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for name, value in (
            ("ram_capacity_bytes", ram_capacity_bytes),
            ("disk_capacity_bytes", disk_capacity_bytes),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        if disk_capacity_bytes < 1:
            raise ValueError("disk_capacity_bytes must be positive.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self.path = Path(path).expanduser()
        if str(path) == ":memory:":
            raise ValueError("MemoryFabric requires a persistent SSD database path.")
        if self.path.is_symlink():
            raise MemoryFabricError("Memory Fabric database must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ram_capacity_bytes = ram_capacity_bytes
        self.disk_capacity_bytes = disk_capacity_bytes
        self._clock = clock
        self._lock = threading.RLock()
        self._ram: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._ram_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._corruptions = 0
        self._closed = False
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_fabric_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    expires_at REAL,
                    created_at REAL NOT NULL,
                    last_access REAL NOT NULL,
                    PRIMARY KEY(namespace, cache_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_fabric_lru "
                "ON memory_fabric_entries(last_access, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise MemoryFabricClosed("Memory Fabric is closed.")

    @classmethod
    def _validate_identity(cls, namespace: str, key: str) -> None:
        if (not isinstance(namespace, str) or not namespace.strip()
                or len(namespace) > cls.MAX_NAMESPACE_LENGTH
                or any(ord(char) < 32 for char in namespace)):
            raise ValueError("namespace must be non-empty bounded text.")
        if (not isinstance(key, str) or not key.strip()
                or len(key) > cls.MAX_KEY_LENGTH
                or any(ord(char) < 32 for char in key)):
            raise ValueError("key must be non-empty bounded text.")

    @classmethod
    def _normalize_payload(cls, value: bytes | bytearray | memoryview) -> bytes:
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TypeError("Memory Fabric accepts bytes-like payloads only.")
        payload = bytes(value)
        if len(payload) > cls.MAX_ENTRY_BYTES:
            raise ValueError("cache entry exceeds the maximum payload size.")
        return payload

    @classmethod
    def _expiry(cls, ttl_seconds: int | float | None, now: float) -> float | None:
        if ttl_seconds is None:
            return None
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not math.isfinite(ttl_seconds)
                or not 0 < ttl_seconds <= cls.MAX_TTL_SECONDS):
            raise ValueError("ttl_seconds must be finite and within the supported range.")
        return now + float(ttl_seconds)

    def _remove_ram(self, identity: tuple[str, str]) -> None:
        previous = self._ram.pop(identity, None)
        if previous is not None:
            self._ram_bytes -= len(previous)

    def _remember_ram(self, identity: tuple[str, str], payload: bytes) -> None:
        self._remove_ram(identity)
        if self.ram_capacity_bytes == 0 or len(payload) > self.ram_capacity_bytes:
            return
        self._ram[identity] = payload
        self._ram_bytes += len(payload)
        while self._ram_bytes > self.ram_capacity_bytes:
            _, evicted = self._ram.popitem(last=False)
            self._ram_bytes -= len(evicted)
            self._evictions += 1

    def put(
        self,
        namespace: str,
        key: str,
        value: bytes | bytearray | memoryview,
        *,
        ttl_seconds: int | float | None = None,
    ) -> None:
        """Persist bytes to SSD and admit them to RAM when they fit."""
        self._validate_identity(namespace, key)
        payload = self._normalize_payload(value)
        if len(payload) > self.disk_capacity_bytes:
            raise ValueError("cache entry exceeds the configured SSD capacity.")
        now = self._clock()
        expires_at = self._expiry(ttl_seconds, now)
        identity = (namespace, key)
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            self._ensure_open()
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM memory_fabric_entries WHERE expires_at IS NOT NULL AND expires_at<=?",
                    (now,),
                )
                connection.execute(
                    "DELETE FROM memory_fabric_entries WHERE namespace=? AND cache_key=?",
                    identity,
                )
                used = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM memory_fabric_entries"
                ).fetchone()[0]
                while used + len(payload) > self.disk_capacity_bytes:
                    oldest = connection.execute(
                        "SELECT namespace,cache_key,size_bytes FROM memory_fabric_entries "
                        "ORDER BY last_access ASC, created_at ASC LIMIT 1"
                    ).fetchone()
                    if oldest is None:
                        raise MemoryFabricError("Unable to free SSD cache capacity.")
                    connection.execute(
                        "DELETE FROM memory_fabric_entries WHERE namespace=? AND cache_key=?",
                        (oldest[0], oldest[1]),
                    )
                    used -= oldest[2]
                    self._evictions += 1
                connection.execute(
                    """
                    INSERT INTO memory_fabric_entries
                    (namespace,cache_key,payload,size_bytes,sha256,expires_at,created_at,last_access)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (namespace, key, sqlite3.Binary(payload), len(payload), digest, expires_at, now, now),
                )
            if expires_at is None:
                self._remember_ram(identity, payload)
            else:
                # TTL entries stay on SSD so RAM hits cannot outlive expiry.
                self._remove_ram(identity)

    def get(self, namespace: str, key: str) -> bytes | None:
        """Return cached bytes, promoting SSD hits into RAM."""
        self._validate_identity(namespace, key)
        identity = (namespace, key)
        now = self._clock()
        with self._lock:
            self._ensure_open()
            cached = self._ram.get(identity)
            if cached is not None:
                self._ram.move_to_end(identity)
                self._hits += 1
                return cached
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT payload,sha256,expires_at FROM memory_fabric_entries "
                    "WHERE namespace=? AND cache_key=?",
                    identity,
                ).fetchone()
                if row is None:
                    self._misses += 1
                    return None
                payload, expected_digest, expires_at = row
                if expires_at is not None and expires_at <= now:
                    connection.execute(
                        "DELETE FROM memory_fabric_entries WHERE namespace=? AND cache_key=?",
                        identity,
                    )
                    self._misses += 1
                    return None
                payload = bytes(payload)
                actual_digest = hashlib.sha256(payload).hexdigest()
                if not hmac.compare_digest(actual_digest, expected_digest):
                    connection.execute(
                        "DELETE FROM memory_fabric_entries WHERE namespace=? AND cache_key=?",
                        identity,
                    )
                    connection.commit()
                    self._remove_ram(identity)
                    self._corruptions += 1
                    raise MemoryFabricCorruption("SSD cache entry failed SHA-256 integrity validation.")
                connection.execute(
                    "UPDATE memory_fabric_entries SET last_access=? WHERE namespace=? AND cache_key=?",
                    (now, namespace, key),
                )
            self._hits += 1
            self._remember_ram(identity, payload)
            return payload

    def delete(self, namespace: str, key: str) -> bool:
        self._validate_identity(namespace, key)
        identity = (namespace, key)
        with self._lock:
            self._ensure_open()
            self._remove_ram(identity)
            with self._connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM memory_fabric_entries WHERE namespace=? AND cache_key=?",
                    identity,
                )
                return cursor.rowcount > 0

    def clear(self, namespace: str | None = None) -> int:
        with self._lock:
            self._ensure_open()
            if namespace is not None:
                self._validate_identity(namespace, "_")
            if namespace is None:
                self._ram.clear()
                self._ram_bytes = 0
                with self._connection() as connection:
                    cursor = connection.execute("DELETE FROM memory_fabric_entries")
            else:
                for identity in tuple(self._ram):
                    if identity[0] == namespace:
                        self._remove_ram(identity)
                with self._connection() as connection:
                    cursor = connection.execute(
                        "DELETE FROM memory_fabric_entries WHERE namespace=?", (namespace,)
                    )
            return cursor.rowcount

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._ensure_open()
            with self._connection() as connection:
                disk_bytes, entries = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes),0),COUNT(*) FROM memory_fabric_entries"
                ).fetchone()
            return {
                "ram_capacity_bytes": self.ram_capacity_bytes,
                "ram_resident_bytes": self._ram_bytes,
                "ram_entries": len(self._ram),
                "disk_capacity_bytes": self.disk_capacity_bytes,
                "disk_used_bytes": disk_bytes,
                "disk_entries": entries,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "corruptions": self._corruptions,
            }

    def close(self) -> None:
        with self._lock:
            self._ram.clear()
            self._ram_bytes = 0
            self._closed = True


__all__ = [
    "MemoryFabric",
    "MemoryFabricClosed",
    "MemoryFabricCorruption",
    "MemoryFabricError",
]
