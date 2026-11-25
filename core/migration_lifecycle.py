"""Provider-neutral domain contract for privileged PostgreSQL migration attempts.

This module validates lifecycle intent only. It deliberately contains no database,
cloud SDK, credential material, network calls, or production adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Protocol, Sequence


class MigrationLifecycleError(RuntimeError):
    """Base error for migration lifecycle contract violations."""


class InvalidMigrationTransition(MigrationLifecycleError):
    """Raised when an attempt requests a forbidden state transition."""


class MigrationAttemptConflict(MigrationLifecycleError):
    """Raised when an attempt version or immutable request conflicts."""


class MigrationState(str, Enum):
    REQUESTED = "requested"
    AUTHORIZED = "authorized"
    IDENTITY_CREATED = "identity_created"
    MEMBERSHIP_GRANTED = "membership_granted"
    CREDENTIAL_ISSUED = "credential_issued"
    MIGRATION_RUNNING = "migration_running"
    MIGRATION_SUCCEEDED = "migration_succeeded"
    MIGRATION_FAILED = "migration_failed"
    CLEANUP_PENDING = "cleanup_pending"
    MEMBERSHIP_REVOKED = "membership_revoked"
    LOGIN_DISABLED = "login_disabled"
    CREDENTIAL_REVOKED = "credential_revoked"
    RETIREMENT_VERIFIED = "retirement_verified"
    COMPLETED = "completed"


_ALLOWED_TRANSITIONS = {
    MigrationState.REQUESTED: frozenset({MigrationState.AUTHORIZED}),
    MigrationState.AUTHORIZED: frozenset({MigrationState.IDENTITY_CREATED, MigrationState.CLEANUP_PENDING}),
    MigrationState.IDENTITY_CREATED: frozenset({MigrationState.MEMBERSHIP_GRANTED, MigrationState.CLEANUP_PENDING}),
    MigrationState.MEMBERSHIP_GRANTED: frozenset({MigrationState.CREDENTIAL_ISSUED, MigrationState.CLEANUP_PENDING}),
    MigrationState.CREDENTIAL_ISSUED: frozenset({MigrationState.MIGRATION_RUNNING, MigrationState.CLEANUP_PENDING}),
    MigrationState.MIGRATION_RUNNING: frozenset({
        MigrationState.MIGRATION_SUCCEEDED, MigrationState.MIGRATION_FAILED,
        MigrationState.CLEANUP_PENDING,
    }),
    MigrationState.MIGRATION_SUCCEEDED: frozenset({MigrationState.CLEANUP_PENDING}),
    MigrationState.MIGRATION_FAILED: frozenset({MigrationState.CLEANUP_PENDING}),
    MigrationState.CLEANUP_PENDING: frozenset({MigrationState.MEMBERSHIP_REVOKED}),
    MigrationState.MEMBERSHIP_REVOKED: frozenset({MigrationState.LOGIN_DISABLED}),
    MigrationState.LOGIN_DISABLED: frozenset({MigrationState.CREDENTIAL_REVOKED}),
    MigrationState.CREDENTIAL_REVOKED: frozenset({MigrationState.RETIREMENT_VERIFIED}),
    MigrationState.RETIREMENT_VERIFIED: frozenset({MigrationState.COMPLETED}),
    MigrationState.COMPLETED: frozenset(),
}

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_MAX_ID_LENGTH = 200
_MAX_SECRET_REFERENCE_LENGTH = 1024


def _identifier(value: str, field: str) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > _MAX_ID_LENGTH or "\x00" in value):
        raise ValueError(f"{field} must be non-empty text of at most {_MAX_ID_LENGTH} characters.")
    return value.strip()


def _code(value: str, field: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase machine-readable code.")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MigrationAttempt:
    """Non-secret durable attempt snapshot; only a secret reference may be retained."""

    attempt_id: str
    target_id: str
    request_digest: str
    state: MigrationState = MigrationState.REQUESTED
    version: int = 0
    secret_reference: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _identifier(self.attempt_id, "attempt_id")
        _identifier(self.target_id, "target_id")
        if not isinstance(self.request_digest, str) or not _DIGEST_RE.fullmatch(self.request_digest):
            raise ValueError("request_digest must be a lowercase SHA-256 hex digest.")
        if not isinstance(self.state, MigrationState):
            raise ValueError("state must be a MigrationState.")
        if type(self.version) is not int or self.version < 0:
            raise ValueError("version must be a non-negative integer.")
        if self.secret_reference is not None:
            if (not isinstance(self.secret_reference, str) or not self.secret_reference.strip()
                    or len(self.secret_reference) > _MAX_SECRET_REFERENCE_LENGTH
                    or "\x00" in self.secret_reference):
                raise ValueError("secret_reference must be a bounded, non-empty opaque reference.")
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        if self.created_at and self.updated_at and self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        if self.state in {
            MigrationState.CREDENTIAL_ISSUED, MigrationState.MIGRATION_RUNNING,
            MigrationState.MIGRATION_SUCCEEDED, MigrationState.MIGRATION_FAILED,
        } and self.secret_reference is None:
            raise ValueError("credential execution states require a secret reference.")


def transition_attempt(
    attempt: MigrationAttempt,
    new_state: MigrationState,
    *,
    expected_version: int,
    now: datetime | None = None,
    secret_reference: str | None = None,
) -> MigrationAttempt:
    """Return a validated next snapshot; persistence must enforce CAS atomically."""
    if not isinstance(attempt, MigrationAttempt):
        raise TypeError("attempt must be a MigrationAttempt.")
    if not isinstance(new_state, MigrationState):
        raise ValueError("new_state must be a MigrationState.")
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer.")
    if attempt.version != expected_version:
        raise MigrationAttemptConflict("stale migration attempt version.")
    if new_state not in _ALLOWED_TRANSITIONS[attempt.state]:
        raise InvalidMigrationTransition(
            f"transition from {attempt.state.value} to {new_state.value} is not permitted."
        )
    timestamp = _utc(now or datetime.now(timezone.utc), "now")
    reference = attempt.secret_reference
    if secret_reference is not None:
        if attempt.secret_reference is not None and secret_reference != attempt.secret_reference:
            raise MigrationAttemptConflict("secret reference is immutable once recorded.")
        if attempt.secret_reference is not None:
            raise MigrationAttemptConflict("secret reference is already recorded.")
        if new_state != MigrationState.CREDENTIAL_ISSUED:
            raise ValueError("secret_reference may only be recorded when credentials are issued.")
        reference = secret_reference
    if new_state in {
        MigrationState.CREDENTIAL_ISSUED, MigrationState.MIGRATION_RUNNING,
        MigrationState.MIGRATION_SUCCEEDED, MigrationState.MIGRATION_FAILED,
    } and reference is None:
        raise ValueError("a secret-manager reference is required before credential execution.")
    if new_state == MigrationState.CREDENTIAL_REVOKED:
        # A missing reference means the controller must independently verify that
        # no credential was ever issued for this attempt. A present reference is
        # retained for post-revocation read-back verification.
        pass
    return replace(
        attempt, state=new_state, version=attempt.version + 1,
        secret_reference=reference, updated_at=timestamp,
    )


@dataclass(frozen=True)
class TargetLease:
    """Fenced lease; generation must increase monotonically per target."""

    target_id: str
    owner_id: str
    generation: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.target_id, "target_id")
        _identifier(self.owner_id, "owner_id")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("generation must be a positive integer.")
        object.__setattr__(self, "expires_at", _utc(self.expires_at, "expires_at"))


@dataclass(frozen=True)
class MigrationExecutionResult:
    """Sanitized runner outcome; never includes SQL text, DSNs, or credential bytes."""

    manifest_digest: str
    applied_migrations: int
    completed_at: datetime
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_digest, str) or not _DIGEST_RE.fullmatch(self.manifest_digest):
            raise ValueError("manifest_digest must be a lowercase SHA-256 hex digest.")
        if type(self.applied_migrations) is not int or self.applied_migrations < 0:
            raise ValueError("applied_migrations must be a non-negative integer.")
        object.__setattr__(self, "completed_at", _utc(self.completed_at, "completed_at"))
        if self.error_code is not None:
            _code(self.error_code, "error_code")


@dataclass(frozen=True)
class MigrationAuditEvent:
    """Secret-free audit envelope with a stable caller-generated event identifier."""

    event_id: str
    attempt_id: str
    event_type: str
    actor_id: str
    occurred_at: datetime
    from_state: MigrationState | None
    to_state: MigrationState
    detail_code: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _identifier(self.attempt_id, "attempt_id")
        _code(self.event_type, "event_type")
        _identifier(self.actor_id, "actor_id")
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if self.from_state is not None and not isinstance(self.from_state, MigrationState):
            raise ValueError("from_state must be a MigrationState or None.")
        if not isinstance(self.to_state, MigrationState):
            raise ValueError("to_state must be a MigrationState.")
        if self.detail_code is not None:
            _code(self.detail_code, "detail_code")


class MigrationAttemptStore(Protocol):
    """Durable store must provide atomic idempotency, CAS, and bounded recovery scans."""

    def create(self, attempt: MigrationAttempt, *, idempotency_key: str) -> MigrationAttempt:
        """Create once; same key and digest returns the original, mismatch conflicts."""
        ...

    def get(self, attempt_id: str) -> MigrationAttempt | None:
        ...

    def transition(
        self, attempt_id: str, *, expected_version: int, new_state: MigrationState,
        secret_reference: str | None = None,
    ) -> MigrationAttempt:
        """Atomically validate and persist a versioned state transition."""
        ...

    def list_recovery_candidates(self, *, before: datetime, limit: int) -> Sequence[MigrationAttempt]:
        """Return a bounded page of non-terminal attempts eligible for reconciliation."""
        ...


class TargetLeaseStore(Protocol):
    """Atomic target-scoped lease store with monotonically increasing fencing generations."""

    def acquire(self, target_id: str, *, owner_id: str, ttl_seconds: int) -> TargetLease | None:
        ...

    def renew(self, lease: TargetLease, *, ttl_seconds: int) -> TargetLease:
        """Reject expired, stale-generation, or non-owner leases."""
        ...

    def release(self, lease: TargetLease) -> None:
        """Release only the exact current owner and fencing generation."""
        ...


class MigrationSecretManager(Protocol):
    """Secret adapter returns references only; raw secret bytes never enter attempt state."""

    def issue(self, *, attempt_id: str, target_id: str) -> str:
        ...

    def revoke(self, secret_reference: str, *, attempt_id: str) -> None:
        ...

    def verify_retired(self, secret_reference: str | None, *, attempt_id: str) -> bool:
        """Verify revocation, or prove no credential was issued when reference is None."""
        ...


class MigrationRunner(Protocol):
    """One-shot runner resolves the secret internally and emits sanitized results only."""

    def execute(
        self, *, attempt_id: str, target_id: str, secret_reference: str,
        manifest_digest: str,
    ) -> MigrationExecutionResult:
        """Never return or persist resolved credential material."""
        ...


class MigrationAuditSink(Protocol):
    """Independent durable audit sink with idempotent append and read-back verification."""

    def append(self, event: MigrationAuditEvent) -> None:
        """Duplicate event IDs with different content must conflict."""
        ...

    def verify_durable(self, event_id: str) -> bool:
        """Return true only after independent read-back confirms durable storage."""
        ...


class MigrationIdentityAdmin(Protocol):
    """Narrow privileged PostgreSQL role operations, implemented by a separate adapter."""

    def create_temporary_login(self, *, attempt_id: str, role_name: str) -> None:
        ...

    def grant_owner_role(self, *, attempt_id: str, login_role: str, owner_role: str) -> None:
        ...

    def revoke_owner_role(self, *, attempt_id: str, login_role: str, owner_role: str) -> None:
        ...

    def terminate_sessions(self, *, attempt_id: str, login_role: str) -> None:
        ...

    def disable_login(self, *, attempt_id: str, login_role: str) -> None:
        ...

    def verify_retired(self, *, attempt_id: str, login_role: str, owner_role: str) -> bool:
        """Verify no login, owner-role membership, or active session remains."""
        ...
