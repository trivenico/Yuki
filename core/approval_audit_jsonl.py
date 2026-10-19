"""Append-only, metadata-only JSONL approval audit with bounded rotation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading


MAX_FILE_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
_HIGH_RISK = {"high", "critical"}
_ALLOWED_EVENTS = {
    "requested", "approved", "denied", "expired", "executed",
    "execution_failed", "execution_blocked", "approval_unavailable",
}


def _safe_text(value, limit=128):
    if value is None:
        return ""
    text = str(value)
    # Single-line bounded metadata only; avoid accidental log injection.
    text = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else " " for ch in text)
    return text[:limit]


class JsonlApprovalAuditLog:
    """Rotating append-only audit. Never accepts prompts or action arguments."""

    def __init__(self, path, *, max_bytes=MAX_FILE_BYTES, backup_count=BACKUP_COUNT):
        if type(max_bytes) is not int or max_bytes < 1024:
            raise ValueError("max_bytes must be an integer >= 1024")
        if type(backup_count) is not int or not 1 <= backup_count <= 10:
            raise ValueError("backup_count must be between 1 and 10")
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()

    def record_event(self, event_type, pending_id="", actor="", session_id="", *, risk="low", status=""):
        if event_type not in _ALLOWED_EVENTS:
            raise ValueError("Unsupported audit event type")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("Unsupported risk level")
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "pending_id": _safe_text(pending_id),
            "actor": _safe_text(actor),
            "session_id": _safe_text(session_id),
            "risk": risk,
            "status": _safe_text(status, 80),
        }
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self._rotate_if_needed(len(payload))
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            fd = os.open(str(self.path), flags, 0o600)
            try:
                with os.fdopen(fd, "ab", closefd=False) as stream:
                    stream.write(payload)
                    stream.flush()
                    if risk in _HIGH_RISK:
                        os.fsync(fd)
            finally:
                os.close(fd)

    def _rotate_if_needed(self, incoming_size):
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size + incoming_size <= self.max_bytes:
            return
        oldest = self.path.with_name(self.path.name + "." + str(self.backup_count))
        try:
            oldest.unlink()
        except FileNotFoundError:
            pass
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(self.path.name + "." + str(index))
            destination = self.path.with_name(self.path.name + "." + str(index + 1))
            try:
                os.replace(source, destination)
            except FileNotFoundError:
                pass
        if self.path.exists():
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
