"""Explicit, foreground-only terminal interaction for paired-device call notifications."""
from __future__ import annotations

from typing import Callable

from automation.remote_call_client import RemoteCallClient


class PairedDeviceCallInbox:
    """Present durable alerts and require a local choice for each invitation.

    Alerts remain unacknowledged when ignored. Accept/decline is performed
    before acknowledgement, so a network failure cannot silently discard an
    invitation. This remains foreground polling, never background push.
    """

    def __init__(
        self,
        client: RemoteCallClient,
        *,
        prompt: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
    ):
        if not all(callable(getattr(client, method, None))
                   for method in ("list_notifications", "acknowledge_notification", "accept", "reject")):
            raise TypeError("client_must_implement_call_notification_protocol")
        if not callable(prompt) or not callable(output):
            raise TypeError("prompt_and_output_must_be_callable")
        self.client = client
        self.prompt = prompt
        self.output = output

    def _handle_notifications(self, notifications) -> dict[str, int]:
        result = {"received": len(notifications), "accepted": 0, "declined": 0, "ignored": 0}
        for notification in notifications:
            self.output(
                "Incoming Yuki call\n"
                f"  From device: {notification.caller_device_id}\n"
                f"  To device: {notification.recipient_device_id}\n"
                f"  Expires: {notification.expires_at}\n"
                "  No microphone or camera will be activated by accepting this invitation."
            )
            choice = self.prompt("Accept, decline, or ignore? [a/d/i] ").strip().lower()
            if choice in {"a", "accept"}:
                self.client.accept(notification.session_id)
                if not self.client.acknowledge_notification(notification.notification_id):
                    raise RuntimeError("call_accepted_but_notification_acknowledgement_failed")
                result["accepted"] += 1
                self.output("Invitation accepted. Media is not connected.")
            elif choice in {"d", "decline"}:
                self.client.reject(notification.session_id)
                if not self.client.acknowledge_notification(notification.notification_id):
                    raise RuntimeError("call_declined_but_notification_acknowledgement_failed")
                result["declined"] += 1
                self.output("Invitation declined.")
            else:
                result["ignored"] += 1
                self.output("Invitation left unanswered.")
        return result

    def run_forever(self, *, poll_interval_seconds: int = 3, sleep=None) -> None:
        """Watch for new alerts in the foreground until the user interrupts."""
        import time
        from collections import deque

        if (isinstance(poll_interval_seconds, bool)
                or not isinstance(poll_interval_seconds, int)
                or not 1 <= poll_interval_seconds <= 30):
            raise ValueError("poll_interval_seconds_must_be_between_1_and_30")
        pause = sleep or time.sleep
        seen = set()
        seen_order = deque()
        self.output(
            f"Watching for Yuki calls in the foreground (checking every {poll_interval_seconds}s). "
            "Press Ctrl-C to stop."
        )
        while True:
            fresh = []
            for notification in self.client.list_notifications():
                if notification.notification_id in seen:
                    continue
                seen.add(notification.notification_id)
                seen_order.append(notification.notification_id)
                fresh.append(notification)
            while len(seen_order) > 1000:
                seen.discard(seen_order.popleft())
            if fresh:
                self._handle_notifications(fresh)
            pause(poll_interval_seconds)

    def run_once(self) -> dict[str, int]:
        notifications = self.client.list_notifications()
        if not notifications:
            self.output("No incoming Yuki calls.")
            return {"received": 0, "accepted": 0, "declined": 0, "ignored": 0}
        return self._handle_notifications(notifications)
