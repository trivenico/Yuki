"""Application-facing sync runtime shared by Yuki CLI and desktop."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

from core.portable_paths import data_root
from core.sync_client import KeyringCredentialVault, SyncClientError, SyncHTTPTransport, SyncOutbox


class SyncRuntime:
    """Owns the local sync endpoint, credential vault and durable outbox.

    Frontends should compose this class rather than implement protocol details.
    Remote application remains injected into SyncAgent until collection handlers
    have been reviewed and wired by the host application.
    """

    def __init__(self, endpoint: str | None = None, root: str | Path | None = None,
                 vault: Any | None = None, transport: Any | None = None):
        self.root = Path(root).expanduser() if root is not None else data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        configured = endpoint if endpoint is not None else os.environ.get("YUKI_SYNC_URL", "")
        self.endpoint = configured.strip().rstrip("/")
        self.vault = vault if vault is not None else KeyringCredentialVault()
        self.transport = transport if transport is not None else (
            SyncHTTPTransport(self.endpoint) if self.endpoint else None
        )
        self.outbox = SyncOutbox(self.root / ".yuki" / "sync" / "outbox.sqlite3")

    def close(self) -> None:
        self.outbox.close()

    def _require_transport(self):
        if self.transport is None:
            raise SyncClientError("Set YUKI_SYNC_URL to your HTTPS Yuki Sync Hub endpoint first.")
        return self.transport

    def pair(self, code: str, device_label: str = "Yuki device") -> str:
        if not isinstance(code, str) or not code.strip() or len(code) > 256:
            raise ValueError("A valid one-time pairing code is required.")
        result = self._require_transport().pair(code.strip(), device_label[:80])
        device_id = result.get("device_id")
        access = result.get("access_token")
        refresh = result.get("refresh_token")
        if not all(isinstance(value, str) and value for value in (device_id, access, refresh)):
            raise SyncClientError("The sync hub returned an invalid pairing response.")
        self.vault.save(device_id, access, refresh)
        return "This device is paired with the Yuki Sync Hub. Credentials were saved to the OS credential vault."

    def status(self) -> str:
        paired = self.vault.load()
        counts = self.outbox.counts()
        endpoint = self.endpoint or "not configured"
        device = paired[0] if paired else "not paired"
        return (
            "Yuki sync status\n"
            f"Hub: {endpoint}\n"
            f"Device: {device}\n"
            f"Outbox: {counts['pending']} pending, {counts['sent']} sent, {counts['conflict']} conflicts\n"
            f"Download cursor: {self.outbox.cursor()}"
        )

    def enqueue_event(self, event: dict[str, Any]) -> bool:
        paired = self.vault.load()
        if not paired:
            raise SyncClientError("Pair this device before queuing sync events.")
        if event.get("device_id") != paired[0]:
            raise SyncClientError("Event device_id does not match this paired device.")
        return self.outbox.enqueue(event)

    def sync_once(self, apply_remote):
        from core.sync_client import SyncAgent
        transport = self._require_transport()
        agent = SyncAgent(transport, self.outbox, self.vault, apply_remote)
        return agent.sync_once()
