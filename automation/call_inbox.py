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
