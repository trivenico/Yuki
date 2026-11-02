"""Content-conscious rotating local operational logs."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

LOGGER_NAME = "yuki"
MAX_LOG_BYTES = 1_000_000
BACKUP_COUNT = 3


def _safe_component(value, limit=120):
    text = re.sub(r"[\r\n\x00-\x1f\x7f]+", " ", str(value))
    return text[:limit]


def configure_logging(log_directory):
    """Configure an idempotent UTF-8 rotating logger with restrictive file mode."""
    directory = Path(log_directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target = (directory / "yuki.log").resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target:
            return logger
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        target, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8", delay=False,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    if os.name == "posix":
        try:
            os.chmod(target, 0o600)
        except OSError:
            logger.removeHandler(handler)
            handler.close()
            raise
    return logger


def log_event(logger, level, event, *, component="runtime", error_type=None):
    """Log bounded operational labels only; never pass prompts, tokens or arguments."""
    parts = [
        f"event={_safe_component(event)!r}",
        f"component={_safe_component(component, 60)!r}",
    ]
    if error_type is not None:
        parts.append(f"error_type={_safe_component(error_type, 80)!r}")
    logger.log(level, " ".join(parts))
