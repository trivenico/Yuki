"""Rotating stdlib logging and privacy-preserving approval audit log."""
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_AUDIT_LOCK = threading.Lock()
_AUDIT_MAX_BYTES = 5 * 1024 * 1024
_AUDIT_BACKUP_COUNT = 3


def setup_logger(workspace_path: Path) -> logging.Logger:
    logs_dir = Path(workspace_path).expanduser() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "yuki.log"

    logger = logging.getLogger("yuki")
    logger.setLevel(logging.DEBUG)
    has_file_handler = any(
        isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", None) == str(log_file.resolve())
        for h in logger.handlers
    )
    if not has_file_handler:
        handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
        ))
        logger.addHandler(handler)
    return logger


def _rotate_audit_if_needed(audit_file: Path) -> None:
    if not audit_file.exists() or audit_file.stat().st_size < _AUDIT_MAX_BYTES:
        return
    oldest = audit_file.with_name(f"{audit_file.name}.{_AUDIT_BACKUP_COUNT}")
    try:
        oldest.unlink()
    except FileNotFoundError:
        pass
    for index in range(_AUDIT_BACKUP_COUNT - 1, 0, -1):
        source = audit_file.with_name(f"{audit_file.name}.{index}")
        target = audit_file.with_name(f"{audit_file.name}.{index + 1}")
        if source.exists():
            os.replace(source, target)
    os.replace(audit_file, audit_file.with_name(f"{audit_file.name}.1"))


def log_approval_audit(
    workspace_path: Path,
    action: str,
    reason: str,
    risk: str,
    decision: bool,
) -> None:
    """Append metadata only; propagate persistence failures so callers fail closed.

    Raw actions and reasons are deliberately excluded because they may contain
    prompts, secrets, user data, or tool arguments.
    """
    workspace = Path(workspace_path).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    audit_file = workspace / "approval_audit.jsonl"
    fingerprint = hashlib.sha256(action.encode("utf-8", errors="replace")).hexdigest()
    record = {
        "action_fingerprint": fingerprint,
        "risk": risk if risk in {"low", "medium", "high"} else "unknown",
        "decision": "approved" if decision is True else "denied",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
    with _AUDIT_LOCK:
        _rotate_audit_if_needed(audit_file)
        with open(audit_file, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            if risk == "high":
                os.fsync(handle.fileno())
