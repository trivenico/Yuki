"""Cross-platform Yuki sync client primitives: durable outbox and HTTPS transport.

The UI/CLI should enqueue approved events and call sync_once from an explicit
background worker. This module never syncs arbitrary files or credentials.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3
import ssl
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from core.sync_contract import validate_sync_event


class SyncClientError(RuntimeError):
    pass


class SyncHTTPError(SyncClientError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class KeyringCredentialVault:
    """Optional OS credential-store adapter (Windows Credential Manager via keyring)."""

    def __init__(self, service: str = "Yuki Sync"):
        try:
            import keyring
        except ImportError as exc:
            raise SyncClientError("Install the optional 'keyring' package for OS credential storage.") from exc
        self._keyring = keyring
        self.service = service

    def save(self, device_id: str, access_token: str, refresh_token: str) -> None:
        self._keyring.set_password(self.service, device_id + ":access", access_token)
        self._keyring.set_password(self.service, device_id + ":refresh", refresh_token)
        self._keyring.set_password(self.service, "active_device", device_id)

    def load(self) -> tuple[str, str, str] | None:
        device_id = self._keyring.get_password(self.service, "active_device")
        if not device_id:
            return None
        access = self._keyring.get_password(self.service, device_id + ":access")
        refresh = self._keyring.get_password(self.service, device_id + ":refresh")
        if not access or not refresh:
            return None
        return device_id, access, refresh

    def delete(self) -> None:
        current = self.load()
        if current:
            device_id = current[0]
            for key in (device_id + ":access", device_id + ":refresh", "active_device"):
                try:
                    self._keyring.delete_password(self.service, key)
                except self._keyring.errors.PasswordDeleteError:
                    pass


class SyncOutbox:
    """Persistent local event queue with retry and conflict states."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize(self):
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_outbox (
                    event_id TEXT PRIMARY KEY,
                    event_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','sent','conflict')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sync_outbox_ready
                    ON sync_outbox(state,next_attempt,created_at);
                CREATE TABLE IF NOT EXISTS sync_client_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    def enqueue(self, raw_event: dict[str, Any]) -> bool:
        event = validate_sync_event(raw_event)
        encoded = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT event_json FROM sync_outbox WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing:
                if existing["event_json"] != encoded:
                    raise SyncClientError("Event ID was reused with different event content.")
                return False
            conn.execute(
                "INSERT INTO sync_outbox(event_id,event_json) VALUES(?,?)",
                (event.event_id, encoded),
            )
            return True

    def ready(self, limit: int = 50, now: float | None = None) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200.")
        now = time.time() if now is None else now
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT event_json FROM sync_outbox
                   WHERE state='pending' AND next_attempt<=?
                   ORDER BY created_at,event_id LIMIT ?""",
                (now, limit),
            ).fetchall()
            return [json.loads(row["event_json"]) for row in rows]

    def mark_sent(self, event_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE sync_outbox SET state='sent',sent_at=CURRENT_TIMESTAMP,last_error=NULL WHERE event_id=?",
                (event_id,),
            )

    def mark_retry(self, event_id: str, error: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT attempts FROM sync_outbox WHERE event_id=? AND state='pending'", (event_id,)
            ).fetchone()
            if row is None:
                return
            attempts = row["attempts"] + 1
            delay = min(3600, 2 ** min(attempts, 11))
            safe_error = str(error)[:300]
            conn.execute(
                "UPDATE sync_outbox SET attempts=?,next_attempt=?,last_error=? WHERE event_id=?",
                (attempts, now + delay, safe_error, event_id),
            )

    def mark_conflict(self, event_id: str, error: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE sync_outbox SET state='conflict',last_error=? WHERE event_id=?",
                (str(error)[:300], event_id),
            )

    def cursor(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM sync_client_state WHERE key='cursor'").fetchone()
            return int(row["value"]) if row else 0

    def set_cursor(self, sequence: int) -> None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < self.cursor():
            raise ValueError("Sync cursor must be a non-negative, monotonic integer.")
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO sync_client_state(key,value) VALUES('cursor',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(sequence),),
            )

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT state,COUNT(*) AS count FROM sync_outbox GROUP BY state").fetchall()
            result = {"pending": 0, "sent": 0, "conflict": 0}
            result.update({row["state"]: row["count"] for row in rows})
            return result


class SyncHTTPTransport:
    """Small HTTPS-only client for the Yuki Sync Hub API."""

    def __init__(self, endpoint: str, timeout: float = 15.0):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Sync endpoint must be an HTTPS URL without embedded credentials.")
        if parsed.query or parsed.fragment:
            raise ValueError("Sync endpoint cannot contain a query or fragment.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
            raise ValueError("timeout must be between 1 and 60 seconds.")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)
        self.context = ssl.create_default_context()

    def _request(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout, context=self.context) as response:
                raw = response.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise SyncClientError("Sync server response exceeded the size limit.")
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            # Do not propagate response bodies: they may contain private data.
            raise SyncHTTPError(exc.code, "Sync server rejected the request.") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SyncClientError("Unable to reach or decode the Yuki Sync Hub.") from None

    def pair(self, code: str, device_label: str = "Yuki device") -> dict[str, Any]:
        return self._request("POST", "/v1/pair", {"code": code, "device_label": device_label})

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._request("POST", "/v1/refresh", {"refresh_token": refresh_token})

    def push(self, event: dict[str, Any], access_token: str) -> dict[str, Any]:
        return self._request("POST", "/v1/events", event, access_token)

    def pull(self, after: int, access_token: str, limit: int = 100) -> dict[str, Any]:
        return self._request("GET", f"/v1/events?after={after}&limit={limit}", token=access_token)

    def acknowledge(self, sequence: int, access_token: str) -> dict[str, Any]:
        return self._request("POST", "/v1/ack", {"sequence": sequence}, access_token)


class SyncAgent:
    """One bounded sync cycle shared by CLI and desktop frontends.

    apply_remote must be idempotent because a process may crash after applying
    an event but before its acknowledgement/cursor is persisted.
    """

    def __init__(
        self,
        transport: SyncHTTPTransport,
        outbox: SyncOutbox,
        vault: KeyringCredentialVault,
        apply_remote: Callable[[dict[str, Any]], None],
    ):
        if not callable(apply_remote):
            raise TypeError("apply_remote must be callable.")
        self.transport = transport
        self.outbox = outbox
        self.vault = vault
        self.apply_remote = apply_remote

    def _credentials(self) -> tuple[str, str, str]:
        saved = self.vault.load()
        if not saved:
            raise SyncClientError("This device is not paired with a Yuki Sync Hub.")
        return saved

    def _refresh(self, device_id: str, refresh_token: str) -> tuple[str, str]:
        rotated = self.transport.refresh(refresh_token)
        if rotated.get("device_id") != device_id:
            self.vault.delete()
            raise SyncClientError("The sync hub returned credentials for a different device.")
        access = rotated.get("access_token")
        refresh = rotated.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise SyncClientError("The sync hub returned an invalid credential response.")
        self.vault.save(device_id, access, refresh)
        return access, refresh

    def sync_once(self, push_limit: int = 50, pull_limit: int = 100) -> dict[str, int]:
        device_id, access, refresh = self._credentials()
        pushed = conflicts = pulled = 0

        for event in self.outbox.ready(push_limit):
            try:
                self.transport.push(event, access)
                self.outbox.mark_sent(event["event_id"])
                pushed += 1
            except SyncHTTPError as exc:
                if exc.status == 401:
                    access, refresh = self._refresh(device_id, refresh)
                    try:
                        self.transport.push(event, access)
                        self.outbox.mark_sent(event["event_id"])
                        pushed += 1
                    except SyncHTTPError as retry_exc:
                        if retry_exc.status == 409:
                            self.outbox.mark_conflict(event["event_id"], "Server revision conflict.")
                            conflicts += 1
                        else:
                            self.outbox.mark_retry(event["event_id"], "HTTP " + str(retry_exc.status))
                            if retry_exc.status in (401, 403):
                                raise SyncClientError("Sync authorization failed; re-pair this device.") from None
                    continue
                if exc.status == 409:
                    self.outbox.mark_conflict(event["event_id"], "Server revision conflict.")
                    conflicts += 1
                elif exc.status in (401, 403):
                    raise SyncClientError("Sync authorization failed; re-pair this device.") from None
                else:
                    self.outbox.mark_retry(event["event_id"], "HTTP " + str(exc.status))
            except SyncClientError:
                self.outbox.mark_retry(event["event_id"], "Network or transport failure.")

        cursor = self.outbox.cursor()
        try:
            page = self.transport.pull(cursor, access, pull_limit)
        except SyncHTTPError as exc:
            if exc.status != 401:
                raise
            access, refresh = self._refresh(device_id, refresh)
            page = self.transport.pull(cursor, access, pull_limit)

        events = page.get("events")
        next_after = page.get("next_after")
        if not isinstance(events, list) or isinstance(next_after, bool) or not isinstance(next_after, int):
            raise SyncClientError("The sync hub returned an invalid event page.")
        if len(events) > pull_limit or next_after < cursor:
            raise SyncClientError("The sync hub returned an invalid event cursor.")
        for item in events:
            if not isinstance(item, dict) or not isinstance(item.get("sequence"), int):
                raise SyncClientError("The sync hub returned a malformed event.")
            event = {key: value for key, value in item.items() if key != "sequence"}
            from core.sync_contract import validate_sync_event
            normalized = validate_sync_event(event)
            self.apply_remote(normalized.to_dict())
            pulled += 1

        if next_after > cursor:
            self.transport.acknowledge(next_after, access)
            self.outbox.set_cursor(next_after)

        return {"pushed": pushed, "pulled": pulled, "conflicts": conflicts}
