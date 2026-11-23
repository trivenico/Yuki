"""Explicit one-notification dispatcher for the durable proactive outbox.

The host invokes dispatch_one() from its own scheduler. Delivery transport is
injected; this module never creates threads or performs network I/O itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.proactive_alerts import ProactiveAlertStore


@dataclass(frozen=True)
class DeliveryResult:
    notification_id: str | None
    status: str


class ProactiveNotificationDispatcher:
    def __init__(
        self,
        store: ProactiveAlertStore,
        deliver: Callable[[dict], bool],
        *,
        max_attempts: int = 5,
        lease_seconds: int = 60,
    ):
        if not isinstance(store, ProactiveAlertStore):
            raise TypeError("store must be a ProactiveAlertStore.")
        if not callable(deliver):
            raise TypeError("deliver must be callable.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300.")
        self._store = store
        self._deliver = deliver
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds

    def dispatch_one(self, *, owner_id: str) -> DeliveryResult:
        """Deliver at most one leased event; failures are durably retryable."""
        event = self._store.claim_notification(
            owner_id=owner_id,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        if event is None:
            return DeliveryResult(notification_id=None, status="empty")
        public_event = {
            key: event[key]
            for key in ("notification_id", "category", "subject", "message", "attempt")
        }
        try:
            delivered = self._deliver(public_event)
            if delivered is not True:
                raise RuntimeError("delivery transport did not confirm success")
        except Exception as exc:
            settled = self._store.settle_notification(
                owner_id=owner_id,
                notification_id=event["notification_id"],
                lease_token=event["lease_token"],
                delivered=False,
                error=exc,
                max_attempts=self._max_attempts,
            )
            return DeliveryResult(
                notification_id=event["notification_id"],
                status="retry_scheduled" if settled else "lease_lost",
            )
        settled = self._store.settle_notification(
            owner_id=owner_id,
            notification_id=event["notification_id"],
            lease_token=event["lease_token"],
            delivered=True,
            max_attempts=self._max_attempts,
        )
        return DeliveryResult(
            notification_id=event["notification_id"],
            status="delivered" if settled else "lease_lost",
        )
