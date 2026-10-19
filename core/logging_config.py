"""Rotating stdlib logging and JSONL approval audit log configuration for Yuki."""
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path


def setup_logger(workspace_path: Path) -> logging.Logger:
    logs_dir = Path(workspace_path).expanduser() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "yuki.log"

    logger = logging.getLogger("yuki")
    logger.setLevel(logging.DEBUG)

    # Check if a file handler for this exact log_file is already present
    has_file_handler = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == str(log_file.resolve())
        for h in logger.handlers
    )
    if not has_file_handler:
        handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def log_approval_audit(workspace_path: Path, action: str, reason: str, risk: str, decision: bool):
    workspace = Path(workspace_path).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    audit_file = workspace / "approval_audit.jsonl"

    record = {
        "action": action,
        "reason": reason,
        "risk": risk,
        "decision": "approved" if decision else "denied",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        with open(audit_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
