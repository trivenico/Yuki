"""Supervised reminder dispatcher with at-least-once delivery semantics.

A delivery channel must be explicitly supplied by the application composition
root. The dispatcher never executes reminder text as an instruction or tool.
Channels must deduplicate using the stable reminder_id idempotency key.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ReminderDispatcher:
    """Poll and deliver leased reminders until stopped.

    The delivery callback receives an inert notification envelope containing
    owner_id, reminder_id, text, due_at and idempotency_key. It must enforce
    owner isolation and idempotency at the actual delivery boundary.
    """

    def __init__(
        self,
        store: Any,
        deliver: Callable[[dict[str, str]], Any],
        *,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 25,
        lease_seconds: int = 60,
    ) -> None:
        if not callable(deliver):
            raise TypeError("deliver must be an explicitly configured delivery channel")
        if not isinstance(poll_interval_seconds, (int, float)) or not 0.1 <= poll_interval_seconds <= 60:
            raise ValueError("invalid_poll_interval")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 100:
            raise ValueError("invalid_batch_size")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 5 <= lease_seconds <= 900:
            raise ValueError("invalid_lease_duration")
        self.store = store
        self.deliver = deliver
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="yuki-reminder-dispatcher")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _store_call(self, method: str, **kwargs):
        return await asyncio.to_thread(getattr(self.store, method), **kwargs)

    async def _renew_lease(self, reminder_id: str, token: str) -> None:
        """Keep a slow in-flight delivery leased until it finishes or loses ownership."""
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._store_call(
                "renew_lease", reminder_id=reminder_id, lease_token=token,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                logger.warning("Reminder lease renewal rejected (reminder_id=%s)", reminder_id)
                raise RuntimeError("reminder_lease_lost")

    async def _deliver_one(self, claim: dict) -> None:
        reminder_id = claim["reminder_id"]
        token = claim["lease_token"]
        envelope = {
            "owner_id": claim["owner_id"],
            "reminder_id": reminder_id,
            "text": claim["text"],
            "due_at": claim["due_at"],
            "idempotency_key": reminder_id,
        }
        # Cancellation or lease replacement may happen after claim but before
        # this worker reaches the delivery boundary. Revalidate immediately
        # before invoking the external channel; this narrows (but cannot make
        # atomic) the race with cancellation during an in-flight send.
        current = await self._store_call(
            "is_lease_current", reminder_id=reminder_id, lease_token=token
        )
        if not current:
            logger.info("Skipping stale or cancelled reminder lease (reminder_id=%s)", reminder_id)
            return
        async def invoke_delivery() -> None:
            if inspect.iscoroutinefunction(self.deliver):
                await self.deliver(envelope)
            else:
                result = await asyncio.to_thread(self.deliver, envelope)
                if inspect.isawaitable(result):
                    await result

        delivery_task = asyncio.create_task(
            invoke_delivery(), name=f"yuki-reminder-send-{reminder_id}"
        )
        renewal_task = asyncio.create_task(
            self._renew_lease(reminder_id, token),
            name=f"yuki-reminder-lease-{reminder_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {delivery_task, renewal_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if renewal_task in done:
                # A rejected renewal means this worker no longer owns delivery.
                # Cancel async channels before they can continue sending. A
                # synchronous callback already running in a worker thread cannot
                # be forcibly stopped; such channels must enforce idempotency.
                try:
                    renewal_task.result()
                except Exception:
                    logger.warning("Stopping delivery after lease loss (reminder_id=%s)", reminder_id)
                delivery_task.cancel()
                try:
                    await delivery_task
                except (asyncio.CancelledError, Exception):
                    pass
                return
            await delivery_task
        except asyncio.CancelledError:
            delivery_task.cancel()
            raise
        except Exception:
            logger.exception("Reminder delivery failed (reminder_id=%s)", reminder_id)
            await self._store_call(
                "record_delivery_failure", reminder_id=reminder_id, lease_token=token
            )
            return
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except (asyncio.CancelledError, Exception):
                pass
        acknowledged = await self._store_call(
            "acknowledge_delivery", reminder_id=reminder_id, lease_token=token
        )
        if not acknowledged:
            logger.warning("Reminder acknowledgement rejected (reminder_id=%s)", reminder_id)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # Claim just in time: pre-leasing a whole batch lets later
                # reminders' leases expire while an earlier slow channel send
                # is still running. Keep the configured batch as a throughput
                # bound, but issue each lease immediately before delivery.
                for _ in range(self.batch_size):
                    if self._stop.is_set():
                        break
                    claims = await self._store_call(
                        "claim_due", limit=1, lease_seconds=self.lease_seconds
                    )
                    if not claims:
                        break
                    await self._deliver_one(claims[0])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Reminder dispatcher polling failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
