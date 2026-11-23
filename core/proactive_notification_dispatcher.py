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
        notifications_enabled_for: Callable[[str], bool],
        max_attempts: int = 5,
        lease_seconds: int = 60,
    ):
        if not isinstance(store, ProactiveAlertStore):
            raise TypeError("store must be a ProactiveAlertStore.")
        if not callable(deliver):
            raise TypeError("deliver must be callable.")
        if not callable(notifications_enabled_for):
            raise TypeError("notifications_enabled_for must be callable.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300.")
        self._store = store
        self._deliver = deliver
        self._notifications_enabled_for = notifications_enabled_for
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds

    def dispatch_one(self, *, owner_id: str) -> DeliveryResult:
        """Deliver at most one leased event; failures are durably retryable."""
        enabled = self._notifications_enabled_for(owner_id)
        if type(enabled) is not bool:
            raise ValueError("notification preference resolver must return boolean.")
        if not enabled:
            return DeliveryResult(notification_id=None, status="disabled")
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
        # Recheck immediately before transport so a preference revocation
        # during claim preparation returns the event to the pending queue.
        enabled = self._notifications_enabled_for(owner_id)
        if type(enabled) is not bool:
            self._store.release_notification(
                owner_id=owner_id, notification_id=event["notification_id"],
                lease_token=event["lease_token"],
            )
            raise ValueError("notification preference resolver must return boolean.")
        if not enabled:
            released = self._store.release_notification(
                owner_id=owner_id, notification_id=event["notification_id"],
                lease_token=event["lease_token"],
            )
            return DeliveryResult(
                notification_id=event["notification_id"],
                status="disabled" if released else "lease_lost",
            )
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
