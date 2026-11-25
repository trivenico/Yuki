"""Test-only fakes for migration lifecycle audit and identity adapter contracts."""
from __future__ import annotations

from threading import RLock
import re

from core.migration_lifecycle import MigrationAttemptConflict, MigrationAuditEvent


class InMemoryMigrationAuditSink:
    """Thread-safe append-only audit fake with explicit read-back verification."""

    def __init__(self):
        self._lock = RLock()
        self._events: dict[str, MigrationAuditEvent] = {}
        self.fail_next_append = False
        self.fail_next_verify = False

    def append(self, event: MigrationAuditEvent) -> None:
        with self._lock:
            if self.fail_next_append:
                self.fail_next_append = False
                raise OSError("injected audit append failure")
            existing = self._events.get(event.event_id)
            if existing is not None and existing != event:
                raise MigrationAttemptConflict("audit event ID reused with different content.")
            self._events[event.event_id] = event

    def verify_durable(self, event_id: str) -> bool:
        with self._lock:
            if self.fail_next_verify:
                self.fail_next_verify = False
                raise OSError("injected independent audit read-back failure")
            return event_id in self._events

    def get(self, event_id: str) -> MigrationAuditEvent | None:
        with self._lock:
            return self._events.get(event_id)


class InMemoryMigrationIdentityAdmin:
    """Test-only model of temporary LOGIN, owner membership, and active sessions."""

    def __init__(self):
        self._lock = RLock()
        self._roles: dict[str, dict[str, object]] = {}
        self._attempt_by_role: dict[str, str] = {}
        self._owner_role_by_attempt: dict[str, str] = {}
        self._owner_roles: dict[str, dict[str, bool]] = {
            "yuki_schema_owner": {
                "login_enabled": False, "superuser": False, "createdb": False,
                "createrole": False, "replication": False, "bypassrls": False,
            }
        }

    @staticmethod
    def _validate_identifier(value: str, label: str) -> None:
        # Model the strict generated-name contract; production SQL must still
        # quote identifiers with the driver rather than interpolate strings.
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
            raise MigrationAttemptConflict(f"invalid PostgreSQL {label} identifier.")

    def _identity(self, attempt_id: str, role_name: str) -> dict[str, object]:
        identity = self._roles.get(attempt_id)
        if identity is None or identity["role_name"] != role_name:
            raise MigrationAttemptConflict("attempt identity does not match role name.")
        return identity

    def create_temporary_login(self, *, attempt_id: str, role_name: str) -> None:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise MigrationAttemptConflict("attempt ID must be non-empty.")
        self._validate_identifier(role_name, "login role")
        with self._lock:
            existing = self._roles.get(attempt_id)
            if existing is not None:
                if existing["role_name"] != role_name:
                    raise MigrationAttemptConflict("attempt already owns a different login role.")
                return
            owner_attempt = self._attempt_by_role.get(role_name)
            if owner_attempt is not None and owner_attempt != attempt_id:
                raise MigrationAttemptConflict("login role name is already bound to another attempt.")
            self._roles[attempt_id] = {
                "role_name": role_name,
                "login_enabled": True,
                "owner_roles": set(),
                "sessions": set(),
            }
            self._attempt_by_role[role_name] = attempt_id

    def grant_owner_role(self, *, attempt_id: str, login_role: str, owner_role: str) -> None:
        self._validate_identifier(login_role, "login role")
        self._validate_identifier(owner_role, "owner role")
        if login_role == owner_role:
            raise MigrationAttemptConflict("temporary LOGIN cannot be its own owner role.")
        with self._lock:
            owner_attributes = self._owner_roles.get(owner_role)
            if owner_attributes is None:
                raise MigrationAttemptConflict("owner role is not registered as an approved schema owner.")
            if any(owner_attributes.values()):
                raise MigrationAttemptConflict("owner role must be NOLOGIN and non-escalating.")
            identity = self._identity(attempt_id, login_role)
            if not identity["login_enabled"]:
                raise MigrationAttemptConflict("disabled login cannot receive owner membership.")
            bound_owner = self._owner_role_by_attempt.get(attempt_id)
            if bound_owner is not None and bound_owner != owner_role:
                raise MigrationAttemptConflict("attempt is bound to a different owner role.")
            self._owner_role_by_attempt[attempt_id] = owner_role
            identity["owner_roles"].add(owner_role)

    def revoke_owner_role(self, *, attempt_id: str, login_role: str, owner_role: str) -> None:
        with self._lock:
            identity = self._identity(attempt_id, login_role)
            identity["owner_roles"].discard(owner_role)

    def add_session(self, *, attempt_id: str, login_role: str, session_id: str) -> None:
        """Test helper representing a PostgreSQL backend session."""
        with self._lock:
            identity = self._identity(attempt_id, login_role)
            identity["sessions"].add(session_id)

    def terminate_sessions(self, *, attempt_id: str, login_role: str) -> None:
        with self._lock:
            identity = self._identity(attempt_id, login_role)
            identity["sessions"].clear()

    def disable_login(self, *, attempt_id: str, login_role: str) -> None:
        with self._lock:
            identity = self._identity(attempt_id, login_role)
            identity["login_enabled"] = False

    def verify_retired(self, *, attempt_id: str, login_role: str, owner_role: str) -> bool:
        with self._lock:
            identity = self._identity(attempt_id, login_role)
            return (
                identity["login_enabled"] is False
                and not identity["owner_roles"]
                and not identity["sessions"]
            )
