"""Bounded delivery worker for the companion notification outbox.

Concrete OS/provider adapters are injected by the application. This module does
not activate devices, access the network, or choose a notification channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from companion.proactive import ReminderQueue


class NotificationSink(Protocol):
    """Adapter contract; implementations must honor idempotency and timeout."""

    def deliver(
        self,
        notification: dict[str, Any],
        *,
        idempotency_key: str,
        timeout_seconds: int,
    ) -> None:
        """Deliver one notification or raise an exception on failure."""


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    delivered: int
    retry_scheduled: int
    lease_lost: int


class NotificationDispatcher:
    """Dispatch one bounded batch; scheduling and process lifecycle are external."""

    def __init__(
        self,
        reminders: ReminderQueue,
        sink: NotificationSink,
        *,
        retry_after_seconds: int = 60,
    ) -> None:
        if not isinstance(reminders, ReminderQueue):
            raise TypeError("reminders must be a ReminderQueue.")
        if not callable(getattr(sink, "deliver", None)):
            raise TypeError("sink must implement deliver().")
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86400:
            raise ValueError("retry_after_seconds must be between 1 and 86400.")
        self.reminders = reminders
        self.sink = sink
        self.retry_after_seconds = retry_after_seconds

    def dispatch_once(
        self,
        now: datetime | None = None,
        *,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> DispatchResult:
        """Deliver at most limit events and return per-batch outcomes.

        Sink implementations must use the supplied stable idempotency key and
        enforce the supplied timeout. Exceptions are reduced to their class name
        before persistence so provider error text cannot leak secrets.
        """
        claims = self.reminders.claim_due_notifications(
            now, lease_seconds=lease_seconds, limit=limit,
        )
        delivered = retry_scheduled = lease_lost = 0
        timeout_seconds = max(1, lease_seconds - 5)
        for claim in claims:
            try:
                self.sink.deliver(
                    claim["payload"],
                    idempotency_key=claim["idempotency_key"],
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                recorded = self.reminders.fail_notification(
                    claim,
                    type(exc).__name__,
                    now,
                    retry_after_seconds=self.retry_after_seconds,
                )
                if recorded:
                    retry_scheduled += 1
                else:
                    lease_lost += 1
                continue
            if self.reminders.complete_notification(claim, now):
                delivered += 1
            else:
                # Provider may already have accepted the event. The stable
                # idempotency key is required to suppress a later duplicate.
                lease_lost += 1
        return DispatchResult(
            claimed=len(claims),
            delivered=delivered,
            retry_scheduled=retry_scheduled,
            lease_lost=lease_lost,
        )
