"""Fail-closed consent and source policy for future desktop observation.

This module authorizes metadata/event sources only. It does not capture screens,
read browser content, or grant permission to execute tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from typing import Callable, Optional


_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_.:/@+-]{0,127}")
_DEFAULT_EXCLUDED_CATEGORIES = frozenset({
    "authentication", "credentials", "financial", "private_communications",
    "private_window", "secrets_manager",
})


def _normalize_identifier(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = value.strip().lower()
    if not normalized or not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be a bounded source/category identifier")
    return normalized


@dataclass(frozen=True)
class ObservationPolicy:
    """Explicit opt-in policy; an empty allowlist authorizes nothing."""

    enabled: bool = False
    allowed_sources: tuple[str, ...] = ()
    denied_sources: tuple[str, ...] = ()
    excluded_categories: frozenset[str] = _DEFAULT_EXCLUDED_CATEGORIES

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be boolean")
        allowed = tuple(_normalize_identifier(item, "allowed source") for item in self.allowed_sources)
        denied = tuple(_normalize_identifier(item, "denied source") for item in self.denied_sources)
        excluded = frozenset(_normalize_identifier(item, "excluded category") for item in self.excluded_categories)
        if len(allowed) > 100 or len(denied) > 100 or len(excluded) > 50:
            raise ValueError("observation policy exceeds its bounded entry limits")
        object.__setattr__(self, "allowed_sources", tuple(dict.fromkeys(allowed)))
        object.__setattr__(self, "denied_sources", tuple(dict.fromkeys(denied)))
        object.__setattr__(self, "excluded_categories", excluded)


class ObservationSession:
    """Owner-consented, revocable authorization gate for observation events.

    The audit sink receives metadata only; event payloads and screen contents
    are deliberately outside this interface.
    """

    def __init__(
        self,
        policy: ObservationPolicy,
        *,
        consent_granted: bool,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ):
        if not isinstance(policy, ObservationPolicy):
            raise TypeError("policy must be an ObservationPolicy")
        if not isinstance(consent_granted, bool):
            raise ValueError("consent_granted must be boolean")
        if audit_sink is not None and not callable(audit_sink):
            raise ValueError("audit_sink must be callable")
        self._policy = policy
        self._consented = consent_granted
        self._audit_sink = audit_sink
        self._state = "active" if consent_granted and policy.enabled else "disabled"
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _record(self, source: str, category: str, allowed: bool, reason: str) -> bool:
        if self._audit_sink is None:
            return True
        event = {
            "event": "observation.authorization",
            "source": source,
            "category": category,
            "allowed": allowed,
            "reason": reason,
        }
        try:
            self._audit_sink(event)
            return True
        except Exception:
            return False

    def authorize(self, source: str, category: str = "general") -> bool:
        """Return whether metadata from this source/category may be observed."""
        try:
            source_id = _normalize_identifier(source, "source")
            category_id = _normalize_identifier(category, "category")
        except ValueError:
            return False
        with self._lock:
            if self._state != "active" or not self._consented:
                allowed, reason = False, "session_not_active"
            elif source_id in self._policy.denied_sources:
                allowed, reason = False, "source_denied"
            elif category_id in self._policy.excluded_categories:
                allowed, reason = False, "sensitive_category"
            elif source_id not in self._policy.allowed_sources:
                allowed, reason = False, "source_not_allowlisted"
            else:
                allowed, reason = True, "explicitly_allowed"
            if not self._record(source_id, category_id, allowed, reason):
                return False
            return allowed

    def pause(self) -> None:
        with self._lock:
            if self._state == "active":
                self._state = "paused"

    def resume(self) -> bool:
        with self._lock:
            if self._state != "paused" or not self._consented or not self._policy.enabled:
                return False
            self._state = "active"
            return True

    def revoke(self) -> None:
        """Irreversibly revoke this session; a new consent session is required."""
        with self._lock:
            self._consented = False
            self._state = "revoked"
