"""Explicit, foreground-only terminal interaction for paired-device call invites."""
from __future__ import annotations

from typing import Callable

from automation.remote_call_client import RemoteCallClient


class PairedDeviceCallInbox:
    """Present incoming invitations and require a local choice for each one.

    This is intentionally a foreground primitive. It does not poll forever,
    spawn a background listener, activate media hardware, or auto-accept calls.
    """

    def __init__(
        self,
        client: RemoteCallClient,
        *,
        prompt: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
    ):
        if not all(callable(getattr(client, method, None))
                   for method in ("list_incoming", "accept", "reject")):
            raise TypeError("client_must_implement_call_inbox_protocol")
        if not callable(prompt) or not callable(output):
            raise TypeError("prompt_and_output_must_be_callable")
        self.client = client
        self.prompt = prompt
        self.output = output

    def _handle_invitations(self, invitations) -> dict[str, int]:
        result = {"received": len(invitations), "accepted": 0, "declined": 0, "ignored": 0}
        for invitation in invitations:
            self.output(
                "Incoming Yuki call\\n"
                f"  From device: {invitation.caller_device_id}\\n"
                f"  To device: {invitation.recipient_device_id}\\n"
                f"  Expires: {invitation.expires_at}\\n"
                "  No microphone or camera will be activated by accepting this invitation."
            )
            choice = self.prompt("Accept, decline, or ignore? [a/d/i] ").strip().lower()
            if choice in {"a", "accept"}:
                self.client.accept(invitation.session_id)
                result["accepted"] += 1
                self.output("Invitation accepted. Media is not connected.")
            elif choice in {"d", "decline"}:
                self.client.reject(invitation.session_id)
                result["declined"] += 1
                self.output("Invitation declined.")
            else:
                result["ignored"] += 1
                self.output("Invitation left unanswered.")
        return result

    def run_forever(self, *, poll_interval_seconds: int = 3, sleep=None) -> None:
        """Watch for new calls in the foreground until the user interrupts."""
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
            for invitation in self.client.list_incoming():
                if invitation.session_id in seen:
                    continue
                seen.add(invitation.session_id)
                seen_order.append(invitation.session_id)
                fresh.append(invitation)
            while len(seen_order) > 1000:
                seen.discard(seen_order.popleft())
            if fresh:
                self._handle_invitations(fresh)
            pause(poll_interval_seconds)

    def run_once(self) -> dict[str, int]:
        invitations = self.client.list_incoming()
        result = {"received": len(invitations), "accepted": 0, "declined": 0, "ignored": 0}
        if not invitations:
            self.output("No incoming Yuki calls.")
            return result

        for invitation in invitations:
            self.output(
                "Incoming Yuki call\n"
                f"  From device: {invitation.caller_device_id}\n"
                f"  To device: {invitation.recipient_device_id}\n"
                f"  Expires: {invitation.expires_at}\n"
                "  No microphone or camera will be activated by accepting this invitation."
            )
            choice = self.prompt("Accept, decline, or ignore? [a/d/i] ").strip().lower()
            if choice in {"a", "accept"}:
                self.client.accept(invitation.session_id)
                result["accepted"] += 1
                self.output("Invitation accepted. Media is not connected.")
            elif choice in {"d", "decline"}:
                self.client.reject(invitation.session_id)
                result["declined"] += 1
                self.output("Invitation declined.")
            else:
                result["ignored"] += 1
                self.output("Invitation left unanswered.")
        return result
