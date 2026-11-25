"""Test-only in-memory adapters for migration lifecycle contract verification.

These fakes deliberately provide no persistence, credentials, networking, or
production configuration path. Their purpose is to exercise adapter semantics.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable

from core.migration_lifecycle import (
    MigrationAttempt,
    MigrationAttemptConflict,
    MigrationState,
    TargetLease,
    transition_attempt,
    validate_new_attempt,
)


class InjectedStoreFailure(RuntimeError):
    """Deterministic test fault; never used by production code."""


class InMemoryMigrationAttemptStore:
    """Thread-safe fake for idempotency, CAS transitions, and recovery scans."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._attempts: dict[str, MigrationAttempt] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self.fail_next_transition = False

    def create(self, attempt: MigrationAttempt, *, idempotency_key: str) -> MigrationAttempt:
        key = validate_new_attempt(attempt, idempotency_key=idempotency_key)
        with self._lock:
            existing = self._idempotency.get(key)
            if existing is not None:
                existing_id, existing_digest = existing
                if existing_digest != attempt.request_digest:
                    raise MigrationAttemptConflict("idempotency key reused with a different request digest.")
                return self._attempts[existing_id]
            if attempt.attempt_id in self._attempts:
                raise MigrationAttemptConflict("attempt_id already exists.")
            now = self._clock().astimezone(timezone.utc)
            created = replace(
                attempt,
                created_at=attempt.created_at or now,
                updated_at=attempt.updated_at or now,
            )
            self._attempts[created.attempt_id] = created
            self._idempotency[key] = (created.attempt_id, created.request_digest)
            return created

    def get(self, attempt_id: str) -> MigrationAttempt | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def transition(
        self,
        attempt_id: str,
        *,
        expected_version: int,
        new_state: MigrationState,
        secret_reference: str | None = None,
    ) -> MigrationAttempt:
        with self._lock:
            current = self._attempts.get(attempt_id)
            if current is None:
                raise KeyError(attempt_id)
            if self.fail_next_transition:
                self.fail_next_transition = False
                raise InjectedStoreFailure("injected transition persistence failure.")
            updated = transition_attempt(
                current,
                new_state,
                expected_version=expected_version,
                now=self._clock(),
                secret_reference=secret_reference,
            )
            self._attempts[attempt_id] = updated
            return updated

    def list_recovery_candidates(
        self, *, before: datetime, limit: int
    ) -> list[MigrationAttempt]:
        if not isinstance(before, datetime) or before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        cutoff = before.astimezone(timezone.utc)
        with self._lock:
            candidates = [
                attempt for attempt in self._attempts.values()
                if attempt.state is not MigrationState.COMPLETED
                and (attempt.updated_at or attempt.created_at) is not None
                and (attempt.updated_at or attempt.created_at) <= cutoff
            ]
            candidates.sort(
                key=lambda item: (item.updated_at or item.created_at, item.attempt_id)
            )
            return candidates[:limit]


class InMemoryTargetLeaseStore:
    """Thread-safe fake with monotonically increasing target fencing generations."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._leases: dict[str, TargetLease] = {}
        self._generations: dict[str, int] = {}

    @staticmethod
    def _ttl(ttl_seconds: int) -> int:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be an integer between 1 and 3600.")
        return ttl_seconds

    def acquire(self, target_id: str, *, owner_id: str, ttl_seconds: int) -> TargetLease | None:
        ttl = self._ttl(ttl_seconds)
        now = self._clock().astimezone(timezone.utc)
        with self._lock:
            current = self._leases.get(target_id)
            if current is not None and current.expires_at > now:
                return None
            generation = self._generations.get(target_id, 0) + 1
            lease = TargetLease(target_id, owner_id, generation, now + timedelta(seconds=ttl))
            self._generations[target_id] = generation
            self._leases[target_id] = lease
            return lease

    def renew(self, lease: TargetLease, *, ttl_seconds: int) -> TargetLease:
        ttl = self._ttl(ttl_seconds)
        now = self._clock().astimezone(timezone.utc)
        with self._lock:
            current = self._leases.get(lease.target_id)
            if current != lease or current is None or current.expires_at <= now:
                raise MigrationAttemptConflict("lease is expired, stale, or not owned by caller.")
            renewed = TargetLease(
                lease.target_id, lease.owner_id, lease.generation,
                now + timedelta(seconds=ttl),
            )
            self._leases[lease.target_id] = renewed
            return renewed

    def release(self, lease: TargetLease) -> None:
        now = self._clock().astimezone(timezone.utc)
        with self._lock:
            current = self._leases.get(lease.target_id)
            if current != lease or current is None or current.expires_at <= now:
                raise MigrationAttemptConflict("lease is expired, stale, or not owned by caller.")
            del self._leases[lease.target_id]
